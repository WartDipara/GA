"""Local image and accessible-control facts, without semantic guesses or device authority."""

import asyncio
import tomllib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from game_automation.common import FrameworkError, digest
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


def view(path, excluded):
    with Image.open(path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for bounds in excluded:
        draw.rectangle(bounds, fill="black")
    return image


def analyze(request):
    image = view(request["image_path"], request["excluded_regions"])
    thumbnail = image.resize((32, 32)).convert("L")
    pixels = list(thumbnail.getdata())
    candidates = []
    for control in request["controls"]:
        if not control["clickable"] and not control["class_name"].endswith("EditText"):
            continue
        candidate = dict(control)
        material = repr((control["resource_id"], control["class_name"], control["bounds"]))
        candidate["target_ref"] = digest(material.encode())[:20]
        candidates.append(candidate)
    difference = None
    previous_path = request["previous_image_path"]
    if previous_path:
        previous = view(previous_path, request["excluded_regions"])
        if previous.size == image.size:
            previous = previous.resize((32, 32)).convert("L")
            difference = ImageStat.Stat(ImageChops.difference(previous, thumbnail)).mean[0] / 255
    result = {
        "screen_key": digest(bytes(pixels)),
        "candidates": candidates,
        "difference": difference,
        "stable": difference is not None and difference < 0.02,
        "excluded_regions": request["excluded_regions"],
        "recognition": "accessible-controls" if candidates else "unknown",
    }
    return result


def create_handler(peer):
    async def handler(method, params):
        if method == "initialize":
            result = {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
            return result
        if method == "health":
            result = {"ok": True}
            return result
        if method == "invoke" and params["capability"] == "perception.analyze":
            return analyze(params["input"])
        raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
