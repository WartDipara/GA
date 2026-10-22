import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw
from runtime import same_runtime, snapshot

from game_automation.common import FrameworkError, digest
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.privacy import credential_regions


def masked_image(data, rectangles):
    image = Image.open(io.BytesIO(data)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for rectangle in rectangles:
        draw.rectangle(rectangle, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def read_controls(device, display):
    try:
        result = await device.run("shell", "uiautomator", "dump", "/dev/tty", allow_failure=True)
        text = result["stdout"]
        start, end = text.find("<?xml"), text.rfind("</hierarchy>")
        if start < 0 or end < 0:
            return []
        root = ET.fromstring(text[start : end + len("</hierarchy>")])
    except FrameworkError as exc:
        if exc.code in (ErrorCode.TRACE_UNAVAILABLE, ErrorCode.CANCELLED, ErrorCode.CLOSED):
            raise
        return []
    except ET.ParseError:
        return []
    controls = []
    for node in root.iter("node"):
        values = node.attrib
        if values.get("package") != display["package_name"]:
            continue
        bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", values.get("bounds", ""))
        if bounds is None:
            continue
        rectangle = list(map(int, bounds.groups()))
        label = values.get("text", "") or values.get("content-desc", "")
        control = {
            "resource_id": values.get("resource-id", ""),
            "label": label,
            "class_name": values.get("class", ""),
            "bounds": rectangle,
            "clickable": values.get("clickable") == "true",
            "sensitive": values.get("password") == "true",
        }
        controls.append(control)
    return controls[:500]


async def capture(device, request):
    before = await snapshot(device, request["package_name"])
    display = before["focused_display"]
    if display is None:
        raise FrameworkError(ErrorCode.DEVICE_OBSERVATION_UNSTABLE)
    controls = await read_controls(device, display)
    rectangles = list(request["sensitive_regions"])
    rectangles.extend(
        item["bounds"]
        for item in controls
        if item["sensitive"]
        or "[REDACTED]" in item["label"]
        or item["class_name"].endswith("EditText")
    )
    workspace = Path(request["workspace"]).resolve()

    captured = await device.run("exec-out", "screencap", "-p", raw=True)
    recognized, matched_username = await credential_regions(captured["data"], device.credentials)
    rectangles.extend(recognized)
    sanitized = masked_image(captured["data"], rectangles)
    with Image.open(io.BytesIO(sanitized)) as dimensions:
        image = {
            "image": {
                "file_name": "screen.png",
                "size_bytes": len(sanitized),
                "sha256": digest(sanitized),
                "width": dimensions.width,
                "height": dimensions.height,
            }
        }
    after = await snapshot(device, request["package_name"])
    if not same_runtime(before, after):
        raise FrameworkError(ErrorCode.DEVICE_OBSERVATION_UNSTABLE)
    if (image["image"]["width"], image["image"]["height"]) != (display["width"], display["height"]):
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "screen.png").write_bytes(sanitized)
    result = {
        "session": device.session,
        "runtime": after,
        "controls": controls,
        "screenshot": image["image"],
        "sensitive_regions": rectangles,
        "credential_profile_match": matched_username,
    }
    return result
