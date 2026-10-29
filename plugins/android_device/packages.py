import asyncio
import hashlib
import re
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


async def inspect(device, request):
    package = request["package_name"]
    listing = await device.run("shell", "pm", "list", "packages", "--user", "0", package)
    lines = listing["stdout"].splitlines()
    if any(not re.fullmatch(r"package:[A-Za-z][A-Za-z0-9_.]*", line) for line in lines):
        raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
    installed = f"package:{package}" in lines
    fingerprint = None
    if installed:
        paths = await device.run("shell", "pm", "path", "--user", "0", package)
        entries = paths["stdout"].splitlines()
        if len(entries) != 1 or not re.fullmatch(r"package:/[A-Za-z0-9_./=+~@-]+\.apk", entries[0]):
            raise FrameworkError(
                ErrorCode.INVALID_ADB_OUTPUT, "A single installed base APK is required"
            )
        path = entries[0].removeprefix("package:")
        hashed = await device.run("shell", "sha256sum", path)
        match = re.fullmatch(r"([0-9a-f]{64})\s+" + re.escape(path) + r"\s*", hashed["stdout"])
        if match is None:
            raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
        fingerprint = match[1]
    disk = await device.run("shell", "df", "-k", "/data")
    rows = [line.split() for line in disk["stdout"].splitlines() if line.strip()]
    try:
        row = rows[-1]
        # A bind mount may report another mountpoint on the queried /data filesystem.
        if (
            len(row) != 6
            or not row[3].isdigit()
            or not row[4].endswith("%")
            or not row[-1].startswith("/")
        ):
            raise ValueError
        available = int(row[3]) * 1024
    except (IndexError, ValueError) as exc:
        raise FrameworkError(
            ErrorCode.INVALID_ADB_OUTPUT, "Android data space is unavailable"
        ) from exc
    result = {
        "session": device.session,
        "package_name": package,
        "installed": installed,
        "sha256": fingerprint,
        "available_bytes": available,
    }
    return result


def verify_source(request):
    path = Path(request["apk_path"])
    if not path.is_file() or path.stat().st_size != request["size_bytes"]:
        raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != request["sha256"]:
        raise FrameworkError(ErrorCode.LOCAL_APK_CHANGED)


async def install(device, request):
    await asyncio.to_thread(verify_source, request)
    state = await inspect(device, request)
    if state["installed"]:
        raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
    if state["available_bytes"] < request["required_bytes"]:
        raise FrameworkError(ErrorCode.DEVICE_SPACE_INSUFFICIENT)
    intent = {"instance_id": device.session["instance_id"], "serial": device.session["serial"]}
    acknowledgement = await device.peer.request("host.action", intent)
    if acknowledgement != {"persisted": True}:
        raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)
    device.commands.timeout = request["install_timeout_seconds"]
    result = await device.run(
        "install",
        "--user",
        "0",
        "--no-incremental",
        "--no-streaming",
        request["apk_path"],
        allow_failure=True,
    )
    output = result["stdout"] + "\n" + result["stderr"]
    if result["exit_code"] == 0 and re.search(r"(?m)^Success\s*$", output):
        status = "installed"
        rejection = None
    else:
        failure = re.search(r"\bFailure \[([A-Z][A-Z0-9_]*)(?::[^\]\r\n]*)?\]", output)
        if failure is None:
            raise FrameworkError(ErrorCode.DEVICE_INSTALL_UNKNOWN)
        status = "rejected"
        rejection = failure[1]
    response = {"session": device.session, "status": status, "rejection_code": rejection}
    return response


async def uninstall(device, request):
    state = await inspect(device, request)
    if not state["installed"]:
        raise FrameworkError(ErrorCode.DEVICE_PACKAGE_MISSING)
    if state["sha256"] != request["installed_sha256"]:
        raise FrameworkError(ErrorCode.DEVICE_PACKAGE_CONFLICT)
    intent = {"instance_id": device.session["instance_id"], "serial": device.session["serial"]}
    acknowledgement = await device.peer.request("host.action", intent)
    if acknowledgement != {"persisted": True}:
        raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)
    result = await device.run("uninstall", request["package_name"], allow_failure=True)
    output = result["stdout"] + "\n" + result["stderr"]
    if result["exit_code"] == 0 and re.search(r"(?m)^Success\s*$", output):
        status = "removed"
        rejection = None
    else:
        failure = re.search(r"\bFailure \[([A-Z][A-Z0-9_]*)(?::[^\]\r\n]*)?\]", output)
        if failure is None:
            raise FrameworkError(ErrorCode.DEVICE_UNINSTALL_UNKNOWN)
        status = "rejected"
        rejection = failure[1]
    response = {"session": device.session, "status": status, "rejection_code": rejection}
    return response
