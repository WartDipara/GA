"""Read-only Android prerequisites with command evidence persisted by the host."""

import asyncio
import re
import shutil
import time
import tomllib
from pathlib import Path

from game_automation.common import FrameworkError, uid
from game_automation.plugin_sdk.rpc import serve, spawn, terminate

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


class CheckBlocked(Exception):
    """A prerequisite failed; its diagnostic is already in the result."""


class EnvironmentCheck:
    def __init__(self, peer, request):
        self.peer = peer
        self.request = request
        self.executable = None
        self.result = {
            "status": "blocked",
            "device_serial": None,
            "package_paths": [],
            "checks": [],
        }

    def require(self, name, condition, code, message, command_id=None):
        check = {
            "name": name,
            "status": "passed" if condition else "blocked",
            "code": "CHECK_PASSED" if condition else code,
            "message": "Check passed." if condition else message,
            "command_id": command_id,
        }
        self.result["checks"].append(check)
        if not condition:
            raise CheckBlocked

    async def event(self, kind, payload):
        event_request = {"kind": "plugin.environment." + kind, "payload": payload}
        acknowledgement = await self.peer.request("host.event", event_request)
        if acknowledgement != {"persisted": True}:
            raise FrameworkError("TRACE_UNAVAILABLE", "Command evidence was not acknowledged")

    async def command(self, name, arguments):
        command_id = uid("cmd")
        command = [self.executable, *arguments]
        started_payload = {"command_id": command_id, "check": name, "arguments": command}
        await self.event("command.started", started_payload)
        started = time.monotonic()
        process = None
        result = {
            "command_id": command_id,
            "exit_code": None,
            "stdout": None,
            "stderr": None,
            "error": None,
        }
        try:
            async with asyncio.timeout(self.request["command_timeout_seconds"]):
                process = await spawn(command)
                stdout, stderr = await process.communicate()
            result["exit_code"] = process.returncode
            result["stdout"] = stdout.decode("utf-8", errors="replace")
            result["stderr"] = stderr.decode("utf-8", errors="replace")
        except TimeoutError:
            result["error"] = "ADB_TIMEOUT"
        except OSError:
            result["error"] = "ADB_START_FAILED"
        finally:
            if process is not None:
                await terminate(process)

        result["duration_ms"] = round((time.monotonic() - started) * 1000)
        kind = "command.incomplete" if result["error"] else "command.completed"
        await self.event(kind, result)
        self.require(
            name,
            result["error"] is None and result["exit_code"] == 0,
            result["error"] or "ADB_COMMAND_FAILED",
            "ADB query failed; inspect its command evidence before resuming.",
            command_id,
        )
        return result

    async def inspect(self):
        self.require(
            "platform",
            self.request["platform"] == "android",
            "UNSUPPORTED_PLATFORM",
            "Environment checks currently support Android only.",
        )
        package = self.request["target"]
        self.require(
            "target",
            PACKAGE_NAME.fullmatch(package) is not None,
            "INVALID_ANDROID_PACKAGE",
            "The target must be an installed Android package identifier; submit a new task.",
        )
        self.executable = shutil.which(self.request["adb_executable"])
        self.require(
            "adb_executable",
            self.executable is not None,
            "ADB_UNAVAILABLE",
            "Install Android Platform Tools or set GAME_AUTO_ADB to its executable.",
        )
        version = await self.command("adb_version", ["version"])
        self.require(
            "adb_version_output",
            version["stdout"].startswith("Android Debug Bridge version "),
            "INVALID_ADB_OUTPUT",
            "The configured executable did not report an ADB version.",
            version["command_id"],
        )

        listing = await self.command("device_list", ["devices", "-l"])
        lines = [line.strip() for line in listing["stdout"].splitlines() if line.strip()]
        self.require(
            "device_list_output",
            bool(lines) and lines[0] == "List of devices attached",
            "INVALID_ADB_OUTPUT",
            "ADB returned an unrecognized device list.",
            listing["command_id"],
        )
        devices = [line.split() for line in lines[1:]]
        self.require(
            "device_entries",
            all(len(device) >= 2 for device in devices)
            and len({device[0] for device in devices}) == len(devices),
            "INVALID_ADB_OUTPUT",
            "ADB returned malformed or duplicate device entries.",
            listing["command_id"],
        )
        selected = self.request["device_serial"]
        candidates = [device for device in devices if selected is None or device[0] == selected]
        self.require(
            "device_selection",
            len(candidates) == 1,
            "DEVICE_SELECTION_REQUIRED",
            "Connect one device or set GAME_AUTO_DEVICE_SERIAL to a listed device.",
            listing["command_id"],
        )
        serial, state = candidates[0][:2]
        self.result["device_serial"] = serial
        self.require(
            "device_connection",
            state == "device",
            "DEVICE_UNAVAILABLE",
            "The selected device is not ready; check its connection and debugging authorization.",
            listing["command_id"],
        )

        boot = await self.command(
            "device_boot", ["-s", serial, "shell", "getprop", "sys.boot_completed"]
        )
        self.require(
            "device_boot_output",
            boot["stdout"].strip() == "1",
            "DEVICE_NOT_BOOTED",
            "Wait for Android to finish booting before resuming.",
            boot["command_id"],
        )
        installed = await self.command("package", ["-s", serial, "shell", "pm", "path", package])
        paths = [line.strip() for line in installed["stdout"].splitlines() if line.strip()]
        self.require(
            "package_paths",
            bool(paths) and all(line.startswith("package:/") for line in paths),
            "PACKAGE_UNAVAILABLE",
            "No installed package paths were confirmed; inspect the package query evidence.",
            installed["command_id"],
        )
        self.result["package_paths"] = [line.removeprefix("package:") for line in paths]
        self.result["status"] = "ready"

    async def run(self):
        try:
            await self.inspect()
        except CheckBlocked:
            pass
        return self.result


def create_handler(peer):
    async def handler(method, params):
        if method == "initialize":
            response = {
                key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")
            }
            return response
        if method == "health":
            response = {"ok": True}
            return response
        if method != "invoke" or params["capability"] != "environment.check":
            raise FrameworkError("METHOD_NOT_FOUND", method)
        return await EnvironmentCheck(peer, params["input"]).run()

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
