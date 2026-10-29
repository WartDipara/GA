"""Bound Android package preparation and focused-display observations over ADB."""

import asyncio
import tomllib
from pathlib import Path

from actions import execute
from capture import capture
from display import observe
from logs import collect
from lifecycle import lifecycle
from packages import inspect, install, uninstall
from runtime import inspect_runtime

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.commands import Commands
from game_automation.plugin_sdk.credentials import load_profile, scrub
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


class Device:
    def __init__(self, peer, commands, session):
        self.peer = peer
        self.commands = commands
        self.session = session

    async def run(self, *arguments, **options):
        command = [self.session["adb_executable"], "-s", self.session["serial"], *arguments]
        return await self.commands.run(command, **options)


def create_handler(peer):
    async def handler(method, params):
        if method == "initialize":
            result = {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
            return result
        if method == "health":
            result = {"ok": True}
            return result
        handlers = {
            "device.inspect": inspect,
            "device.install": install,
            "device.uninstall": uninstall,
            "device.observe": observe,
            "device.runtime": inspect_runtime,
            "device.capture": capture,
            "device.execute": execute,
            "device.logs": collect,
            "device.lifecycle": lifecycle,
        }
        if method != "invoke" or params["capability"] not in handlers:
            raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)
        request = params["input"]
        session_request = {"capability": "emulator.session", "input": request["session"]}
        session = await peer.request("host.call", session_request, timeout=None)
        commands = Commands(
            peer, request["session"]["command_timeout_seconds"], namespace="android"
        )
        device = Device(peer, commands, session)
        device.credentials = (
            load_profile(request["credential_path"]) if request.get("credential_path") else {}
        )
        commands.sanitize = lambda text: scrub(text, device.credentials)
        return await handlers[params["capability"]](device, request)

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
