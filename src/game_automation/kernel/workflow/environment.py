import os

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.errors import ErrorCode


async def check_environment(store, run_id, host, manifest, task):
    settings = manifest["application"]["environment"]
    request = {
        "platform": task["platform"],
        "target": task["target"],
        "adb_executable": os.environ.get("GAME_AUTO_ADB", "adb"),
        "device_serial": os.environ.get("GAME_AUTO_DEVICE_SERIAL") or None,
        "command_timeout_seconds": settings["command_timeout_seconds"],
    }
    validate(host.contracts, "environment-request", request)
    timeout = request["command_timeout_seconds"] * 4 + 15
    result = await host.call("environment.check", request, timeout=timeout)
    call = next(
        event
        for event in reversed(store.events(run_id))
        if event["kind"] == "call.started" and event["payload"]["capability"] == "environment.check"
    )
    trace_ids = {key: call[key] for key in ("span_id", "parent_span_id", "request_id")}
    store.event(run_id, "environment.checked", result, **trace_ids)
    if result["status"] == "blocked":
        blocker = next(check for check in result["checks"] if check["status"] == "blocked")
        raise FrameworkError(ErrorCode.ENVIRONMENT_BLOCKED, blocker["message"])
    return result
