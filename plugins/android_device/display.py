import re
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def focused_display(text):
    focused = re.search(r"\bmTopFocusedDisplayId=(\d+)", text)
    if focused is None:
        raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT, "Focused display is unavailable")
    display_id = int(focused[1])
    sections = re.split(r"(?m)^  Display: mDisplayId=(\d+)[^\n]*\n", text)
    candidates = [
        sections[index + 1]
        for index in range(1, len(sections), 2)
        if int(sections[index]) == display_id
    ]
    if len(candidates) != 1:
        raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT, "Focused display is ambiguous")
    section = candidates[0]
    rotation = re.search(r"(?m)^\s+mRotation=([0-3])\b", section)
    dimensions = re.search(r"\bcur=(\d+)x(\d+)\b", section)
    focus = re.search(r"(?m)^\s*mCurrentFocus=([^\n]+)", section)
    component = re.search(r"\bu\d+ ([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)", focus[1] if focus else "")
    if rotation is None or dimensions is None or component is None:
        raise FrameworkError(ErrorCode.DEVICE_OBSERVATION_UNSTABLE)
    width, height = int(dimensions[1]), int(dimensions[2])
    if not 0 < width <= 16384 or not 0 < height <= 16384:
        raise FrameworkError(ErrorCode.INVALID_ADB_OUTPUT)
    result = {
        "display_id": display_id,
        "rotation_degrees": int(rotation[1]) * 90,
        "width": width,
        "height": height,
        "package_name": component[1],
        "activity": component[2],
    }
    return result


async def observe(device, request):
    before = focused_display((await device.run("shell", "dumpsys", "window"))["stdout"])
    workspace = Path(request["workspace"]).resolve()
    screenshot = workspace / "screen.png"
    captured = await device.run("exec-out", "screencap", "-p", screenshot=screenshot)
    after = focused_display((await device.run("shell", "dumpsys", "window"))["stdout"])
    image = captured["image"]
    if before != after or (image["width"], image["height"]) != (after["width"], after["height"]):
        raise FrameworkError(ErrorCode.DEVICE_OBSERVATION_UNSTABLE)
    result = {"session": device.session, "display": after, "screenshot": image}
    return result
