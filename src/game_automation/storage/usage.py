"""Incremental evidence accounting; rebuild file state after process restart."""

import time

from game_automation.common import FrameworkError
from game_automation.errors import ErrorCode


class FileUsage:
    def __init__(self, directory):
        self.directory = directory
        self.files = {}
        self.size_bytes = 0
        self.reconciled = 0

    def track(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.directory.resolve()):
            raise FrameworkError(ErrorCode.INVALID_PATH)
        previous = self.files.get(path, 0)
        current = path.stat().st_size if path.is_file() else 0
        self.size_bytes += current - previous
        if current:
            self.files[path] = current
        else:
            self.files.pop(path, None)

    def scan(self, directory=None):
        directory = directory or self.directory
        if directory == self.directory:
            files = {}
            for path in directory.rglob("*"):
                if path.is_file():
                    resolved = path.resolve()
                    if not resolved.is_relative_to(self.directory.resolve()):
                        raise FrameworkError(ErrorCode.INVALID_PATH)
                    files[resolved] = path.stat().st_size
            self.files = files
            self.size_bytes = sum(files.values())
            self.reconciled = time.monotonic()
            return
        for path in directory.rglob("*"):
            if path.is_file():
                self.track(path)

    def total(self):
        if time.monotonic() - self.reconciled >= 300:
            self.scan()
        return self.size_bytes
