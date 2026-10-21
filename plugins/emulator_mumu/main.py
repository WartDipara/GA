"""Discover existing MuMu instances without changing applications or emulator settings."""

import asyncio
import json
import re
import tomllib
from pathlib import Path

from commands import Commands
from discovery import adb_endpoint, resolve_tools

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


async def inspect_instance(commands, tools, raw, capture, workspace):
    instance = {
        "index": int(raw["index"]),
        "name": raw.get("name", ""),
        "status": "stopped",
        "serial": None,
        "android_version": None,
        "abis": [],
        "width": None,
        "height": None,
        "shell_uid": None,
        "screenshot": None,
        "error": None,
    }
    if not raw.get("is_process_started"):
        return instance
    instance["status"] = "starting"
    if not raw.get("is_android_started"):
        return instance
    try:
        serial = adb_endpoint(raw)
        instance["serial"] = serial
        await commands.run([tools["adb"], "connect", serial])

        async def query(*arguments):
            result = await commands.run([tools["adb"], "-s", serial, *arguments])
            return result["stdout"].strip()

        if await query("get-state") != "device":
            raise FrameworkError(ErrorCode.DEVICE_UNAVAILABLE)
        if await query("shell", "getprop", "sys.boot_completed") != "1":
            raise FrameworkError(ErrorCode.DEVICE_NOT_BOOTED)
        instance["android_version"] = await query("shell", "getprop", "ro.build.version.release")
        abis = await query("shell", "getprop", "ro.product.cpu.abilist")
        instance["abis"] = [abi for abi in abis.split(",") if abi]
        display = await query("shell", "wm", "size")
        sizes = re.findall(r"(?:Physical|Override) size: ([0-9]+)x([0-9]+)", display)
        identity = re.search(r"\buid=([0-9]+)", await query("shell", "id"))
        if not sizes or identity is None or not instance["android_version"] or not instance["abis"]:
            raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
        instance["width"], instance["height"] = map(int, sizes[-1])
        instance["shell_uid"] = int(identity[1])
        if capture:
            path = within(workspace, f"instance_{instance['index']}.png")
            command = [tools["adb"], "-s", serial, "exec-out", "screencap", "-p"]
            result = await commands.run(command, screenshot=path)
            instance["screenshot"] = result["image"]
            instance["width"] = result["image"]["width"]
            instance["height"] = result["image"]["height"]
        instance["status"] = "ready"
    except FrameworkError as exc:
        if exc.code in (ErrorCode.TRACE_UNAVAILABLE, ErrorCode.CANCELLED, ErrorCode.CLOSED):
            raise
        instance["status"] = "blocked"
        instance["error"] = exc.as_dict()
    return instance


async def discover(peer, request):
    commands = Commands(peer, request["command_timeout_seconds"])
    tools = await resolve_tools(commands, request)
    listing = await commands.run([tools["manager"], "info", "-v", "all"])
    try:
        raw_instances = json.loads(listing["stdout"])
        if not isinstance(raw_instances, dict) or len(raw_instances) > 1000:
            raise ValueError
        instances = []
        for raw in raw_instances.values():
            index = int(raw["index"])
            if index < 0 or str(index) != str(raw["index"]):
                raise ValueError
            if request["instance_index"] is None or index == request["instance_index"]:
                instances.append(raw)
        indices = [int(raw["index"]) for raw in instances]
        if len(indices) != len(set(indices)):
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise FrameworkError(ErrorCode.INVALID_MUMU_OUTPUT) from exc
    if request["instance_index"] is not None and not instances:
        raise FrameworkError(
            ErrorCode.DEVICE_SELECTION_REQUIRED, "Requested MuMu instance not found"
        )
    results = []
    workspace = Path(request["workspace"]).resolve()
    for raw in sorted(instances, key=lambda item: int(item["index"])):
        result = await inspect_instance(commands, tools, raw, request["capture"], workspace)
        results.append(result)
    ready = any(instance["status"] == "ready" for instance in results)
    result = {"provider": "mumu", "status": "ready" if ready else "blocked", "instances": results}
    return result


def create_handler(peer):
    async def handler(method, params):
        if method == "initialize":
            result = {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
            return result
        if method == "health":
            result = {"ok": True}
            return result
        if method != "invoke" or params["capability"] != "emulator.discover":
            raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)
        return await discover(peer, params["input"])

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
