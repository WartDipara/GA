"""Package-scoped shutdown and explicit restoration without replaying gameplay actions."""

import asyncio

from packages import inspect
from runtime import snapshot

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def has_processes(runtime):
    return any(not process["state"].startswith("Z") for process in runtime["processes"])


async def lifecycle(device, request):
    package = request["package_name"]
    installed = await inspect(device, request)
    if installed["sha256"] != request["sha256"]:
        raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
    before = await snapshot(device, package)
    after = before
    closing = request["operation"] == "close"
    status = "unknown"
    if closing and not has_processes(before):
        status = "already_closed"
    elif not closing and before["alive"]:
        status = "already_running"
    elif closing or not has_processes(before):
        intent = {"instance_id": device.session["instance_id"], "serial": device.session["serial"]}
        acknowledgement = await device.peer.request("host.action", intent)
        if acknowledgement != {"persisted": True}:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)
        if closing:
            await device.run("shell", "am", "force-stop", "--user", "0", package)
        else:
            arguments = {
                "capability": "emulator.activate",
                "input": {"session": request["session"], "package_name": package},
            }
            await device.peer.request("host.call", arguments, timeout=None)
        for attempt in range(3):
            after = await snapshot(device, package)
            if closing and not has_processes(after):
                status = "closed"
                break
            if not closing and after["alive"]:
                status = "restored"
                break
            if attempt < 2:
                await asyncio.sleep(0.25)
    result = {"session": device.session, "before": before, "after": after, "status": status}
    return result
