import shutil

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


def free_disk_bytes(directory):
    try:
        return shutil.disk_usage(directory).free
    except OSError as exc:
        raise FrameworkError(ErrorCode.DISK_SPACE_UNAVAILABLE) from exc


def insufficient_disk_space(required_bytes, available_bytes):
    gib = 1024**3
    message = (
        f"Insufficient disk space: need {required_bytes / gib:.2f} GiB "
        f"({required_bytes} bytes), available {available_bytes / gib:.2f} GiB "
        f"({available_bytes} bytes). Free disk space and resume."
    )
    return FrameworkError(ErrorCode.DISK_SPACE_INSUFFICIENT, message)


def require_disk_space(directory, required_bytes):
    available_bytes = free_disk_bytes(directory)
    if available_bytes < required_bytes:
        raise insufficient_disk_space(required_bytes, available_bytes)
    return available_bytes
