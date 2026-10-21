import hashlib
import json
import re

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode


def read_checkpoint(workspace, request):
    path = within(workspace, "download.json")
    if not path.exists():
        state = new_checkpoint(request["source_id"])
        return state
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state["source_id"] != request["source_id"]:
            raise ValueError
        count = state["downloaded_bytes"]
        total = state.get("total_bytes")
        if type(count) is not int or not 0 <= count <= request["max_apk_bytes"]:
            raise ValueError
        if total is not None and (
            type(total) is not int or not count <= total <= request["max_apk_bytes"]
        ):
            raise ValueError
        prefix = state["prefix_sha256"]
        if not isinstance(prefix, str) or not re.fullmatch(r"[0-9a-f]{64}", prefix):
            raise ValueError
        for field in ("transfer_complete", "archive_invalid"):
            if field in state and type(state[field]) is not bool:
                raise ValueError
        retry_fields = (("retry_not_before", 0), ("retry_backoff_seconds", 1))
        for field, minimum in retry_fields:
            value = state.get(field, minimum)
            if type(value) not in (int, float) or not minimum <= value < float("inf"):
                raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise FrameworkError(ErrorCode.DOWNLOAD_STATE_INVALID) from exc
    return state


def new_checkpoint(source_id, previous=None):
    state = {
        "source_id": source_id,
        "downloaded_bytes": 0,
        "prefix_sha256": hashlib.sha256().hexdigest(),
    }
    if previous:
        for field in ("retry_not_before", "retry_backoff_seconds"):
            if field in previous:
                state[field] = previous[field]
    return state


def complete_checkpoint(state):
    count = state.get("downloaded_bytes", 0)
    return bool(
        count
        and not state.get("archive_invalid")
        and (state.get("transfer_complete") or state.get("total_bytes") == count)
    )


def file_checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256")


def file_version(stat):
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return version
