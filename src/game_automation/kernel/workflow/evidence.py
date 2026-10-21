import hashlib

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.images import png_dimensions


def verify_image(run_dir, workspace_ref, image):
    path = within(within(run_dir, workspace_ref), image["file_name"])
    try:
        if path.stat().st_size != image["size_bytes"]:
            raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
        with path.open("rb") as stream:
            dimensions = png_dimensions(stream.read(24))
            stream.seek(0)
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT) from exc
    if dimensions != (image["width"], image["height"]) or actual != image["sha256"]:
        raise FrameworkError(ErrorCode.INVALID_SCREENSHOT)
    image["evidence_ref"] = f"{workspace_ref}/{image['file_name']}"
