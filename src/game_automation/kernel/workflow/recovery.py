"""Reconcile abandoned call boundaries under the process execution lock; never dispatch actions."""

from game_automation.errors import ErrorCode

BOUNDARIES = {
    "call.started": ("call.completed", "call.failed", "call.incomplete"),
    "gameplay.step.started": (
        "gameplay.step.completed",
        "gameplay.step.failed",
        "gameplay.step.incomplete",
    ),
    "workflow.stage.started": (
        "workflow.stage.completed",
        "workflow.stage.failed",
        "workflow.stage.incomplete",
    ),
}


def reconcile_interrupted_runs(store):
    """The caller owns execution.lock, proving no framework execution still owns these runs."""
    runs = store.db.execute("SELECT id FROM runs WHERE status='running' ORDER BY rowid").fetchall()
    for row in runs:
        run_id = row[0]
        events = store.events(
            run_id, kinds=tuple(kind for kinds in BOUNDARIES.values() for kind in kinds)
        )
        closed = {event["span_id"] for event in events}
        for event in store.iter_events(run_id, kinds=tuple(BOUNDARIES)):
            if event["span_id"] in closed:
                continue
            payload = {
                **event["payload"],
                "error": {"code": ErrorCode.PREVIOUS_PROCESS_EXITED},
                "status": "unknown",
                "duration_ms": None,
            }
            store.event(
                run_id,
                event["kind"].removesuffix("started") + "incomplete",
                payload,
                span_id=event["span_id"],
                parent_span_id=event["parent_span_id"],
                request_id=event["request_id"],
            )
        reason = {"code": ErrorCode.PREVIOUS_PROCESS_EXITED}
        store.transition(run_id, "blocked", reason=reason)
