"""Image processing and DPAPI tools without workflow or database access."""

import asyncio
import hashlib
import math
import os
import tempfile
import tomllib
from pathlib import Path

from PIL import Image

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.credentials import load_profile
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


def prepare_credential(request):
    source = Path(request["profile_path"])
    destination = Path(request["destination"])
    load_profile(source)
    data = source.read_bytes()
    revision = hashlib.sha256(data).hexdigest()
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != revision:
            raise FrameworkError(ErrorCode.CREDENTIAL_UNAVAILABLE)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    result = {"revision": revision}
    return result


def prepare_image(request):
    destination = Path(request["destination"])
    with Image.open(request["source"]) as image:
        native_width, native_height = image.size
        scale = min(1, 1280 / max(image.size), 768 / min(image.size))
        width = max(1, math.floor(native_width * scale))
        height = max(1, math.floor(native_height * scale))
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.resize((width, height), Image.Resampling.LANCZOS).save(destination, format="PNG")
    result = {
        "width": width,
        "height": height,
        "native_width": native_width,
        "native_height": native_height,
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
        if method == "invoke":
            capability = params["capability"]
            if capability == "credential.prepare":
                return await asyncio.to_thread(prepare_credential, params["input"])
            if capability == "planner.image":
                return await asyncio.to_thread(prepare_image, params["input"])
        raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
