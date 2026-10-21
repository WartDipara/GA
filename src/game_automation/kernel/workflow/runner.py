import asyncio

from game_automation.common import FrameworkError
from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.kernel.policy import ExecutionLock
from game_automation.observability import export_trace
from game_automation.prompting import freeze, load

from .intake import intake

BLOCKED = {
    "MISSING_CAPABILITY",
    "SKILL_MISSING",
    "INVALID_SKILL",
    "AI_UNAVAILABLE",
    "AI_REQUIRES_INPUT",
    "CODEX_UNAVAILABLE",
    "CODEX_VERSION_MISMATCH",
    "TIMEOUT",
    "PROCESS_EXITED",
    "SNAPSHOT_MISSING",
    "SNAPSHOT_CHANGED",
    "RUNTIME_CHANGED",
    "ACTION_ALREADY_DISPATCHED",
    "BUDGET_EXHAUSTED",
    "DEVICE_NOT_IMPLEMENTED",
    "INTAKE_NEEDS_INPUT",
    "INTAKE_NEW_RUN_REQUIRED",
}


def new_run(store, task_id, mode="game"):
    run_id = store.create_run(task_id, mode)
    try:
        freeze(store, run_id)
    except FrameworkError as exc:
        store.transition(run_id, "blocked", reason=exc.as_dict())
    return run_id


async def execute(store, run_id):
    with ExecutionLock(store.var / "execution.lock"):
        run = store.run(run_id)
        if run["status"] in ("succeeded", "failed", "cancelled"):
            raise FrameworkError("RUN_TERMINAL", "Use a new execution batch for a terminal run")
        if run["mode"] not in ("game", "intake"):
            raise FrameworkError("RUN_MODE_REMOVED", "This run mode is no longer supported")
        try:
            if run["status"] == "running":
                events = store.events(run_id)
                closed = {
                    event["span_id"]
                    for event in events
                    if event["kind"] in ("call.completed", "call.failed", "call.incomplete")
                }
                for event in events:
                    if event["kind"] == "call.started" and event["span_id"] not in closed:
                        store.event(
                            run_id,
                            "call.incomplete",
                            {
                                "error": {"code": "PREVIOUS_PROCESS_EXITED"},
                                "result": "unknown",
                                "attempt": event["payload"]["attempt"],
                            },
                            span_id=event["span_id"],
                            parent_span_id=event["parent_span_id"],
                            request_id=event["request_id"],
                        )
                store.transition(run_id, "blocked", reason={"code": "PREVIOUS_PROCESS_EXITED"})
            if store.run(run_id)["cancel_requested"]:
                store.transition(run_id, "cancelled")
                export_trace(store, run_id)
                return store.run(run_id)
            manifest = load(store, run_id)
            if store.unknown_actions(run_id):
                raise FrameworkError(
                    "ACTION_ALREADY_DISPATCHED",
                    "Uncertain actions need reconciliation; resume cannot replay them",
                )
            store.transition(run_id, "running")
            host = Host(store, run_id, Router(manifest["plugins"]), manifest)
            task = store.task(run["task_id"])
            timeout = (
                manifest["application"].get("intake", {}).get("timeout_seconds", 300)
                if task.get("kind") == "document" or run["mode"] == "intake"
                else task["limits"]["timeout_seconds"]
            )
            async with asyncio.timeout(timeout):
                store.transition(run_id, "running", "intake")
                task = await intake(store, run_id, host, manifest)
                if run["mode"] == "game":
                    store.transition(run_id, "running", "environment")
                    await host.call("environment.check", {"task": task})
                    raise FrameworkError(
                        "DEVICE_NOT_IMPLEMENTED", "Game execution is not available"
                    )
            host.check_cancel()
            store.transition(run_id, "succeeded", "intake")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                error = FrameworkError("CANCELLED", "Execution interrupted")
            elif isinstance(exc, TimeoutError):
                error = FrameworkError("BUDGET_EXHAUSTED", "Run time limit exceeded")
            else:
                error = (
                    exc
                    if isinstance(exc, FrameworkError)
                    else FrameworkError("INTERNAL_ERROR", type(exc).__name__)
                )
            status = (
                "cancelled"
                if error.code == "CANCELLED"
                else "blocked"
                if error.code in BLOCKED
                else "failed"
            )
            # Persistence failure must escape, not become a fabricated terminal record.
            store.transition(run_id, status, reason=error.as_dict())
        export_trace(store, run_id)
        return store.run(run_id)
