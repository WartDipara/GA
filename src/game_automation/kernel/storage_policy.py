from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def storage_settings(manifest):
    settings = manifest["application"].get("storage", {})
    values = (settings.get("reserve_bytes"), settings.get("overhead_bytes_per_apk"))
    margin = settings.get("safety_margin_percent")
    if (
        any(type(value) is not int or value < 1 for value in values)
        or type(margin) is not int
        or not 1 <= margin <= 100
    ):
        raise FrameworkError(ErrorCode.INVALID_STORAGE_POLICY)
    return settings
