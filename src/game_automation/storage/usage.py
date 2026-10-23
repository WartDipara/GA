"""Incremental evidence accounting; rebuild file state after process restart."""

import time

from game_automation.common import FileLock, FrameworkError
from game_automation.errors import ErrorCode


def initialize_event_usage(store):
    exists = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_usage'"
    ).fetchone()
    if exists:
        return
    with FileLock(store.var / "execution.lock"):
        store.db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE event_usage (
                run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                size_bytes INTEGER NOT NULL
            );
            INSERT INTO event_usage
                SELECT run_id,SUM(LENGTH(CAST(data AS BLOB))) FROM events GROUP BY run_id;
            CREATE TRIGGER event_usage_insert AFTER INSERT ON events BEGIN
                INSERT INTO event_usage VALUES(NEW.run_id,LENGTH(CAST(NEW.data AS BLOB)))
                ON CONFLICT(run_id) DO UPDATE SET size_bytes=size_bytes+excluded.size_bytes;
            END;
            CREATE TRIGGER event_usage_delete AFTER DELETE ON events BEGIN
                UPDATE event_usage SET size_bytes=size_bytes-LENGTH(CAST(OLD.data AS BLOB))
                WHERE run_id=OLD.run_id;
            END;
            CREATE TRIGGER event_usage_update AFTER UPDATE ON events BEGIN
                UPDATE event_usage SET size_bytes=size_bytes-LENGTH(CAST(OLD.data AS BLOB))
                WHERE run_id=OLD.run_id;
                INSERT INTO event_usage VALUES(NEW.run_id,LENGTH(CAST(NEW.data AS BLOB)))
                ON CONFLICT(run_id) DO UPDATE SET size_bytes=size_bytes+excluded.size_bytes;
            END;
            COMMIT;
            """
        )


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
