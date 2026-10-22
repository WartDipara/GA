"""Evidence-only gameplay report; generating it never contacts a model or device."""

from collections import Counter


def gameplay_report(store, run_id):
    events = store.events(run_id)
    task = store.task(store.run(run_id)["task_id"])

    def selected(kind):
        values = [event["payload"] for event in events if event["kind"] == kind]
        return values

    starts = selected("call.started")
    decisions = selected("gameplay.decision")
    actions = selected("action.dispatched")
    origins = selected("gameplay.action.origin")
    waits = selected("gameplay.wait")
    reasons = Counter(item["reason"] for item in selected("gameplay.planner.requested"))
    report = {
        "run_id": run_id,
        "state": store.run(run_id)["status"],
        "goal": task["goal"],
        "ai_calls": sum(item["capability"] == "ai.decide" for item in starts),
        "ai_trigger_reasons": dict(reasons),
        "deterministic_steps": sum(item["source"] != "planner" for item in origins),
        "dispatched_actions": len(actions),
        "decisions": decisions,
        "wait_duration_ms": sum(item["duration_ms"] for item in waits),
        "corrections": selected("gameplay.correction"),
        "cache_hits": selected("gameplay.cache.hit"),
        "cache_invalidations": selected("gameplay.cache.invalidated"),
        "verified_business_goals": selected("gameplay.goal.completed"),
        "outcomes": selected("gameplay.outcome"),
        "battle_progress": selected("gameplay.battle.observed"),
        "stage_advances": selected("gameplay.battle.advanced"),
        "usage": selected("ai.usage") or None,
        "checkpoint": (selected("gameplay.checkpoint") or [None])[-1],
        "observations": [
            {"observation_id": item["observation_id"], "evidence_ref": item["evidence_ref"]}
            for item in selected("gameplay.observation")
        ],
        "coverage": "Only recorded reachable interactions; no authoritative whole-game inventory",
    }
    store.artifact(run_id, "reports/gameplay.json", report)
    return report
