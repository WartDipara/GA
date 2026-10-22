"""Explicit image coordinates at the planner boundary; device evidence stays native."""

import math
from copy import deepcopy
from dataclasses import dataclass

from PIL import Image

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


@dataclass(frozen=True)
class ImageCoordinates:
    native_width: int
    native_height: int
    width: int
    height: int

    def describe(self):
        dimensions = {
            "space": "planner_image_pixels",
            "width": self.width,
            "height": self.height,
            "native_width": self.native_width,
            "native_height": self.native_height,
        }
        return dimensions

    def rectangle(self, bounds, *, to_native=False):
        if to_native and not (
            0 <= bounds[0] < bounds[2] <= self.width and 0 <= bounds[1] < bounds[3] <= self.height
        ):
            raise FrameworkError(ErrorCode.AI_OUTPUT_INVALID, "Target is outside the planner image")
        scale_x = self.native_width / self.width
        scale_y = self.native_height / self.height
        if not to_native:
            scale_x = 1 / scale_x
            scale_y = 1 / scale_y
        rectangle = [
            math.floor(bounds[0] * scale_x),
            math.floor(bounds[1] * scale_y),
            math.ceil(bounds[2] * scale_x),
            math.ceil(bounds[3] * scale_y),
        ]
        return rectangle

    def action(self, action, *, to_native=False):
        if action is None:
            return None
        result = deepcopy(action)
        scale_x = self.native_width / self.width
        scale_y = self.native_height / self.height
        if not to_native:
            scale_x = 1 / scale_x
            scale_y = 1 / scale_y
        for x_key, y_key in (("x", "y"), ("end_x", "end_y")):
            x, y = action[x_key], action[y_key]
            if to_native and not (0 <= x < self.width and 0 <= y < self.height):
                raise FrameworkError(
                    ErrorCode.AI_OUTPUT_INVALID, "Point is outside the planner image"
                )
            result[x_key] = math.floor(x * scale_x)
            result[y_key] = math.floor(y * scale_y)
        if result["bounds"] is not None:
            result["bounds"] = self.rectangle(result["bounds"], to_native=to_native)
        return result


def planner_image(source, destination):
    with Image.open(source) as image:
        native_width, native_height = image.size
        scale = min(1, 1280 / max(image.size), 768 / min(image.size))
        size = (max(1, math.floor(native_width * scale)), max(1, math.floor(native_height * scale)))
        coordinates = ImageCoordinates(native_width, native_height, *size)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.resize(size, Image.Resampling.LANCZOS).save(destination, format="PNG")
    return coordinates


def planner_perception(facts, coordinates):
    projected = deepcopy(facts)
    for candidate in projected["candidates"]:
        candidate["bounds"] = coordinates.rectangle(candidate["bounds"])
    projected["excluded_regions"] = [
        coordinates.rectangle(bounds) for bounds in facts["excluded_regions"]
    ]
    return projected


def device_decision(decision, coordinates):
    projected = deepcopy(decision)
    projected["action"] = coordinates.action(decision["action"], to_native=True)
    projected["sensitive_regions"] = [
        coordinates.rectangle(bounds, to_native=True) for bounds in decision["sensitive_regions"]
    ]
    return projected
