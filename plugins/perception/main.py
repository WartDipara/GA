"""Local image and accessible-control facts, without semantic guesses or device authority."""

import asyncio
import tomllib
from pathlib import Path

from locating import inside, load_image, locate
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
        if (
            not control["clickable"]
            and not control.get("scrollable")
            and not control["class_name"].endswith("EditText")
        ):
            continue
        candidate = dict(control)
        material = repr(
            (
                control["resource_id"],
                control["class_name"],
                control["label"],
                control.get("container_ref", ""),
            )
        )
        candidate["target_ref"] = digest(material.encode())[:20]
        candidates.append(candidate)
    difference = None
    motion_regions = []
    previous_path = request["previous_image_path"]
    if previous_path:
        previous = view(previous_path, request["excluded_regions"])
        if previous.size == image.size:
            previous = previous.resize((32, 32)).convert("L")
            difference = ImageStat.Stat(ImageChops.difference(previous, thumbnail)).mean[0] / 255
            for top in range(0, 32, 4):
                for left in range(0, 32, 4):
                    tile = (left, top, left + 4, top + 4)
                    delta = ImageChops.difference(previous.crop(tile), thumbnail.crop(tile))
                    if ImageStat.Stat(delta).mean[0] / 255 > 0.08:
                        motion_regions.append(
                            [
                                left * image.width // 32,
                                top * image.height // 32,
                                (left + 4) * image.width // 32,
                                (top + 4) * image.height // 32,
                            ]
                        )
    result = {
        "screen_key": digest(bytes(pixels)),
        "candidates": candidates,
        "difference": difference,
        "stable": difference is not None and difference < 0.02,
        "excluded_regions": request["excluded_regions"],
        "recognition": "accessible-controls" if candidates else "unknown",
        "motion_regions": motion_regions,
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
        if method == "invoke" and params["capability"] == "perception.locate":
            return locate(params["input"])
        if method == "invoke" and params["capability"] == "perception.viewport":
            request = params["input"]
            image = load_image(request["image_path"])
            if not inside(request["bounds"], image.size):
                raise FrameworkError(ErrorCode.STALE_OBSERVATION)
            crop = image.crop(request["bounds"]).resize((128, 128)).convert("L")
            crop = crop.point(lambda value: value // 16 * 16)
            result = {"key": digest(crop.tobytes())}
            return result
        raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
