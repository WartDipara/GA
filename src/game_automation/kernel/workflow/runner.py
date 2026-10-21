import asyncio

from game_automation.common import FrameworkError, now
from game_automation.contracts import schema, validate
from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.kernel.policy import ExecutionLock
from game_automation.observability import export_trace
from game_automation.prompting import build_input, freeze, load

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


def fixture_observation(store, run_id):
    path = store.run_dir(run_id) / "artifacts/fixture.svg"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480"><rect width="320" height="480" fill="#eef"/><text x="20" y="40">FIXTURE ONLY - NOT A GAME</text><rect x="20" y="80" width="100" height="50" fill="#bbb"/><text x="30" y="110">Settings</text></svg>',
        encoding="utf-8",
    )
    observation = {
        "id": "obs_fixture",
        "session_id": run_id,
        "captured_at": now(),
        "width": 320,
        "height": 480,
        "orientation": "portrait",
        "evidence_refs": ["artifacts/fixture.svg"],
        "fixture": True,
    }
    ref = store.artifact(run_id, "artifacts/observation.json", observation)
    store.event(
        run_id,
        "observation.captured",
        {"observation": observation, "evidence_ref": "artifacts/fixture.svg", "context_ref": ref},
    )
    return observation


async def demo(store, run_id, host):
    # A transport/trace demonstration, not an implementation of automatic gameplay.
    for stage in ("intake", "environment", "launch"):
        store.transition(run_id, "running", stage)
        await host.call("fixture.echo", {"stage": stage, "fixture": True})
    store.transition(run_id, "running", "exploration")
    observation = fixture_observation(store, run_id)
    action = {
        "id": "action_fixture_1",
        "type": "tap",
        "observation_id": observation["id"],
        "session_id": run_id,
        "target_ref": "fixture_settings",
        "x": 60,
        "y": 100,
    }
    decision = {
        "observation_id": observation["id"],
        "evidence_refs": observation["evidence_refs"],
        "objective": "Test framework evidence links",
        "reason_summary": "Fixture has a labelled settings target.",
        "proposed_action": action,
        "expected_outcome": "Fixture transport returns an execution result",
        "uncertainties": [],
    }
    turn = build_input(
        store,
        run_id,
        "exploration",
        {"observation": observation},
        schema(store.run_dir(run_id) / "contracts", "decision"),
    )
    result = await host.call("fixture.decide", {"turn": turn, "decision": decision})
    validate(store.run_dir(run_id) / "contracts", "decision", result["decision"])
    store.event(run_id, "decision.proposed", {"decision": decision, "fixture": True})
    existing = store.db.execute(
        "SELECT result,status FROM actions WHERE run_id=? AND action_id=?", (run_id, action["id"])
    ).fetchone()
    if existing:
        if existing["status"] == "unknown":
            raise FrameworkError("ACTION_ALREADY_DISPATCHED", "Unknown action cannot be replayed")
        store.event(
            run_id, "action.reused_record", {"action_id": action["id"], "executed_again": False}
        )
    else:
        await host.call("fixture.act", {"action": action, "observation": observation})
    store.event(
        run_id,
        "verification.unavailable",
        {
            "fixture": True,
            "reason": "No real device or post-action observation; not counted as verified",
        },
    )
    store.transition(run_id, "running", "reporting")
    await host.call("fixture.nested", {"stage": "reporting", "fixture": True})


async def codex(store, run_id, host, manifest, probe):
    workspace = store.run_dir(run_id) / "ai-workspace"
    workspace.mkdir(exist_ok=True)
    params = {
        "codex": manifest["application"]["codex"],
        "workspace": str(workspace),
        "role": "exploration",
    }
    if not probe:
        observation = fixture_observation(store, run_id)
        output_schema = schema(store.run_dir(run_id) / "contracts", "decision")
        params.update(
            {
                "thread_id": store.session(run_id, "exploration"),
                "turn": build_input(
                    store,
                    run_id,
                    "exploration",
                    {
                        "task": store.task(store.run(run_id)["task_id"]),
                        "observation": observation,
                        "supplements": [
                            e["payload"]
                            for e in store.events(run_id)
                            if e["kind"] == "task.supplement"
                        ],
                        "available_actions": ["stop"],
                        "instruction": "This is a framework inference smoke check with synthetic observation. Return a stop action with id smoke_stop and evidence refs from the observation; no device operation is available.",
                    },
                    output_schema,
                ),
            }
        )
    result = await host.call(
        "ai.probe" if probe else "ai.decide",
        params,
        timeout=manifest["application"]["codex"]["timeout_seconds"] + 45,
    )
    if not probe:
        validate(store.run_dir(run_id) / "contracts", "decision", result["decision"])
        decision = result["decision"]
        if (
            decision["observation_id"] != observation["id"]
            or decision["proposed_action"]["session_id"] != run_id
            or decision["proposed_action"]["type"] != "stop"
        ):
            raise FrameworkError(
                "AI_OUTPUT_INVALID", "Smoke decision does not match the bounded request"
            )
        store.event(
            run_id, "decision.proposed", {"decision": decision, "fixture": True, "executed": False}
        )
    store.event(run_id, "codex.check", result)


async def execute(store, run_id):
    with ExecutionLock(store.var / "execution.lock"):
        run = store.run(run_id)
        if run["status"] in ("succeeded", "failed", "cancelled"):
            raise FrameworkError("RUN_TERMINAL", "Use a new execution batch for a terminal run")
        try:
            if run["status"] == "running":
                events = store.events(run_id)
                closed = {
                    e["span_id"]
                    for e in events
                    if e["kind"] in ("call.completed", "call.failed", "call.incomplete")
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
                if run["mode"] == "demo":
                    await demo(store, run_id, host)
                elif run["mode"] in ("codex-probe", "codex-smoke"):
                    await codex(store, run_id, host, manifest, run["mode"] == "codex-probe")
                else:
                    store.transition(run_id, "running", "intake")
                    task = await intake(store, run_id, host, manifest)
                    if run["mode"] != "intake":
                        store.transition(run_id, "running", "environment")
                        # This boundary remains blocked until device checks are implemented.
                        await host.call("environment.check", {"task": task})
                        raise FrameworkError(
                            "DEVICE_NOT_IMPLEMENTED", "Game execution is not available"
                        )
            host.check_cancel()
            store.transition(
                run_id, "succeeded", "intake" if run["mode"] == "intake" else "reporting"
            )
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
