import asyncio

from game_automation.common import FrameworkError
from game_automation.disk import require_disk_space
from game_automation.errors import ErrorCode
from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.kernel.policy import ExecutionLock
from game_automation.kernel.policy.storage import storage_settings
from game_automation.observability import export_trace
from game_automation.prompting import freeze, load

from .device import observe_device, prepare_device
from .emulators import discover_emulators
from .environment import check_environment
from .intake import intake
from .preprocessing import preprocess

BLOCKED = {
    ErrorCode.DEVICE_APK_REQUIRED,
    ErrorCode.DEVICE_BINDING_CONFLICT,
    ErrorCode.DEVICE_IDENTITY_CHANGED,
    ErrorCode.DEVICE_PACKAGE_CONFLICT,
    ErrorCode.DEVICE_SPACE_INSUFFICIENT,
    ErrorCode.DEVICE_INSTALL_REJECTED,
    ErrorCode.DEVICE_INSTALL_UNKNOWN,
    ErrorCode.DEVICE_OBSERVATION_UNSTABLE,
    ErrorCode.DEVICE_NOT_BOOTED,
    ErrorCode.DEVICE_UNAVAILABLE,
    ErrorCode.ADB_UNAVAILABLE,
    ErrorCode.INVALID_ADB_OUTPUT,
    ErrorCode.INVALID_MUMU_OUTPUT,
    ErrorCode.INVALID_SCREENSHOT,
    ErrorCode.LOCAL_COMMAND_FAILED,
    ErrorCode.MUMU_UNAVAILABLE,
    ErrorCode.MUMU_SELECTION_REQUIRED,
    ErrorCode.AAPT2_UNAVAILABLE,
    ErrorCode.AAPT2_TIMEOUT,
    ErrorCode.DOWNLOAD_STATE_INVALID,
    ErrorCode.DOWNLOAD_REDIRECT_LIMIT,
    ErrorCode.DOWNLOAD_RETRY_DEFERRED,
    ErrorCode.MISSING_CAPABILITY,
    ErrorCode.SKILL_MISSING,
    ErrorCode.INVALID_SKILL,
    ErrorCode.AI_UNAVAILABLE,
    ErrorCode.AI_REQUIRES_INPUT,
    ErrorCode.CODEX_UNAVAILABLE,
    ErrorCode.CODEX_VERSION_MISMATCH,
    ErrorCode.TIMEOUT,
    ErrorCode.PROCESS_EXITED,
    ErrorCode.SNAPSHOT_MISSING,
    ErrorCode.SNAPSHOT_CHANGED,
    ErrorCode.RUNTIME_CHANGED,
    ErrorCode.ACTION_ALREADY_DISPATCHED,
    ErrorCode.BUDGET_EXHAUSTED,
    ErrorCode.DEVICE_NOT_IMPLEMENTED,
    ErrorCode.INTAKE_NEEDS_INPUT,
    ErrorCode.INTAKE_NEW_RUN_REQUIRED,
    ErrorCode.ENVIRONMENT_BLOCKED,
    ErrorCode.APK_INPUT_REQUIRED,
    ErrorCode.APK_SOURCE_CHANGED,
    ErrorCode.PREPROCESSING_BLOCKED,
    ErrorCode.PREPROCESSING_INTERRUPTED,
    ErrorCode.TRACE_UNAVAILABLE,
    ErrorCode.DISK_SPACE_INSUFFICIENT,
    ErrorCode.DISK_SPACE_UNAVAILABLE,
    ErrorCode.DISK_BUDGET_EXCEEDED,
}


def new_run(store, task_id, mode="game"):
    run_id = store.create_run(task_id, mode)
    try:
        freeze(store, run_id)
    except FrameworkError as exc:
        store.transition(run_id, "blocked", reason=exc.as_dict())
    return run_id


async def execute(store, run_id, source_file=None):
    with ExecutionLock(store.var / "execution.lock"):
        run = store.run(run_id)
        if run["status"] in ("succeeded", "failed", "cancelled"):
            raise FrameworkError(ErrorCode.RUN_TERMINAL)
        if run["mode"] not in (
            "game",
            "intake",
            "preprocessing",
            "emulators",
            "device",
            "observation",
        ):
            raise FrameworkError(ErrorCode.RUN_MODE_REMOVED)
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
                        incomplete_payload = {
                            "error": {"code": ErrorCode.PREVIOUS_PROCESS_EXITED},
                            "result": "unknown",
                            "attempt": event["payload"]["attempt"],
                        }
                        store.event(
                            run_id,
                            "call.incomplete",
                            incomplete_payload,
                            span_id=event["span_id"],
                            parent_span_id=event["parent_span_id"],
                            request_id=event["request_id"],
                        )
                store.transition(
                    run_id, "blocked", reason={"code": ErrorCode.PREVIOUS_PROCESS_EXITED}
                )
            if store.run(run_id)["cancel_requested"]:
                store.transition(run_id, "cancelled")
                export_trace(store, run_id)
                return store.run(run_id)
            manifest = load(store, run_id)
            if run["mode"] != "device" and store.unknown_actions(run_id):
                raise FrameworkError(ErrorCode.ACTION_ALREADY_DISPATCHED)
            store.transition(run_id, "running")
            storage = storage_settings(manifest)
            require_disk_space(store.run_dir(run_id), storage["reserve_bytes"])
            host = Host(store, run_id, Router(manifest["plugins"]), manifest)
            task = store.task(run["task_id"])
            if run["mode"] in ("emulators", "device", "observation"):
                timeout = 0
            elif run["mode"] == "preprocessing":
                timeout = manifest["application"]["preprocessing"]["batch_timeout_seconds"]
            elif task.get("kind") == "document" or run["mode"] == "intake":
                timeout = manifest["application"].get("intake", {}).get("timeout_seconds", 300)
            else:
                timeout = task["limits"]["timeout_seconds"]
            async with asyncio.timeout(timeout or None):
                if run["mode"] == "emulators":
                    store.transition(run_id, "running", "environment")
                    await discover_emulators(store, run_id, host, manifest, task)
                elif run["mode"] == "device":
                    store.transition(run_id, "running", "environment")
                    await prepare_device(store, run_id, host, manifest, task)
                elif run["mode"] == "observation":
                    store.transition(run_id, "running", "environment")
                    await observe_device(store, run_id, host, manifest, task)
                elif run["mode"] == "preprocessing":
                    store.transition(run_id, "running", "preprocessing")
                    await preprocess(store, run_id, host, manifest, task, source_file)
                else:
                    store.transition(run_id, "running", "intake")
                    task = await intake(store, run_id, host, manifest)
                if run["mode"] == "game":
                    store.transition(run_id, "running", "environment")
                    await check_environment(store, run_id, host, manifest, task)
                    store.transition(run_id, "running", "launch")
                    raise FrameworkError(
                        ErrorCode.DEVICE_NOT_IMPLEMENTED, "Game launch is not available"
                    )
            host.check_cancel()
            stages = {
                "preprocessing": "preprocessing",
                "emulators": "environment",
                "device": "environment",
                "observation": "environment",
            }
            stage = stages.get(run["mode"], "intake")
            store.transition(run_id, "succeeded", stage)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                if run["mode"] == "preprocessing" and not store.run(run_id)["cancel_requested"]:
                    error = FrameworkError(ErrorCode.PREPROCESSING_INTERRUPTED)
                else:
                    error = FrameworkError(ErrorCode.CANCELLED, "Execution interrupted")
            elif isinstance(exc, TimeoutError):
                error = FrameworkError(ErrorCode.BUDGET_EXHAUSTED)
            else:
                error = (
                    exc
                    if isinstance(exc, FrameworkError)
                    else FrameworkError(ErrorCode.INTERNAL_ERROR, type(exc).__name__)
                )
            if error.code == ErrorCode.CANCELLED:
                status = "cancelled"
            elif error.code in BLOCKED:
                status = "blocked"
            else:
                status = "failed"
            # Persistence failure must escape, not become a fabricated terminal record.
            store.transition(run_id, status, reason=error.as_dict())
        export_trace(store, run_id)
        return store.run(run_id)
