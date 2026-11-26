import os
import tempfile
from pathlib import Path

from game_automation.errors import ErrorCode, FrameworkError

from .values import json_text


class FileLock:
    """OS-owned exclusive lock shared by the kernel and isolated plugin workers."""

    def __init__(self, path: Path, busy_code=ErrorCode.RUN_BUSY):
        self.path = path
        self.busy_code = busy_code

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise FrameworkError(self.busy_code) from exc
        return self

    def __exit__(self, *_):
        self.file.close()


def within(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise FrameworkError(ErrorCode.INVALID_PATH)
    return candidate


def atomic_text(path: Path, contents: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def atomic_json(path: Path, value):
    atomic_text(path, json_text(value))
