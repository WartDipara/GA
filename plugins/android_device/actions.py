import asyncio
import io
import shlex
from pathlib import Path

from capture import masked_image
from PIL import Image, ImageChops, ImageStat
from runtime import same_runtime, snapshot

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


async def execute(device, request):
    action = request["action"]
    current = await snapshot(device, request["package_name"])
    if not same_runtime(request["runtime"], current):
        raise FrameworkError(ErrorCode.STALE_OBSERVATION)
    kind = action["type"]
    display = current["focused_display"]
    if kind not in ("launch", "return", "wait") and display is None:
        raise FrameworkError(ErrorCode.STALE_OBSERVATION)
    if kind in ("tap", "swipe", "long_press", "credential"):
        x, y = action["x"], action["y"]
        if not 0 <= x < display["width"] or not 0 <= y < display["height"]:
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
        captured = await device.run("exec-out", "screencap", "-p", raw=True)
        current_image = Image.open(
            io.BytesIO(masked_image(captured["data"], request["sensitive_regions"]))
        ).convert("RGB")
        reference = Image.open(Path(request["observation_path"])).convert("RGB")
        if current_image.size != reference.size:
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
        bounds = action["bounds"] or [
            max(0, x - 32),
            max(0, y - 32),
            min(display["width"], x + 33),
            min(display["height"], y + 33),
        ]
        left, top, right, bottom = bounds
        if not (
            0 <= left <= x < right <= display["width"]
            and 0 <= top <= y < bottom <= display["height"]
        ):
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
        difference = ImageChops.difference(current_image.crop(bounds), reference.crop(bounds))
        if sum(ImageStat.Stat(difference).mean) / (3 * 255) > 0.12:
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
    if kind == "return" and not current["alive"]:
        raise FrameworkError(ErrorCode.GAMEPLAY_BLOCKED)
    if kind == "credential":
        value = device.credentials[action["credential_field"]]
        if not value.isascii() or any(c.isspace() for c in value):
            raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE)
    if kind == "swipe":
        if (
            not 0 <= action["end_x"] < display["width"]
            or not 0 <= action["end_y"] < display["height"]
        ):
            raise FrameworkError(ErrorCode.STALE_OBSERVATION)
    if not same_runtime(current, await snapshot(device, request["package_name"])):
        raise FrameworkError(ErrorCode.STALE_OBSERVATION)
    intent = {"instance_id": device.session["instance_id"], "serial": device.session["serial"]}
    acknowledgement = await device.peer.request("host.action", intent)
    if acknowledgement != {"persisted": True}:
        raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)
    if kind == "wait":
        await asyncio.sleep(action["duration_ms"] / 1000)
    elif kind in ("launch", "return"):
        arguments = {
            "capability": "emulator.activate",
            "input": {"session": request["session"], "package_name": request["package_name"]},
        }
        await device.peer.request("host.call", arguments, timeout=None)
    elif kind == "credential":
        script = f"input -d {display['display_id']} tap {action['x']} {action['y']}\n"
        script += f"input -d {display['display_id']} text {shlex.quote(value)}\nexit\n"
        await device.run("shell", stdin=script.encode(), sensitive=True)
    else:
        prefix = ["shell", "input", "-d", str(display["display_id"])]
        if kind == "back":
            arguments = [*prefix, "keyevent", "4"]
        elif kind == "tap":
            arguments = [*prefix, "tap", str(action["x"]), str(action["y"])]
        else:
            end_x = action["end_x"] if kind == "swipe" else action["x"]
            end_y = action["end_y"] if kind == "swipe" else action["y"]
            arguments = [
                *prefix,
                "swipe",
                str(action["x"]),
                str(action["y"]),
                str(end_x),
                str(end_y),
                str(action["duration_ms"]),
            ]
        await device.run(*arguments)
    result = {"status": "executed", "requires_verification": True}
    return result
