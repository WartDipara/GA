from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from game_automation.common import FrameworkError, atomic_json, now, redact, uid, within
from game_automation.errors import ErrorCode


def run_subject(task):
    if task.get("kind") == "apk_batch":
        return f"{len(task['source_ids'])}-apks"
    if task.get("kind") == "document":
        return Path(task["filename"]).stem
    return task.get("target") or "task"


class Store:
    """Only the kernel owns this database. Events and state commit together."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.var = self.root / "var"
        self.var.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.var / "state.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise FrameworkError(ErrorCode.SCHEMA_VERSION)
        schema_sql = """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                submission_key TEXT UNIQUE,
                spec TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES tasks(id),
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                cancel_requested INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                run_id TEXT REFERENCES runs(id),
                span_id TEXT,
                kind TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS event_run ON events(run_id, seq);
            CREATE TABLE IF NOT EXISTS sessions (
                run_id TEXT,
                role TEXT,
                thread_id TEXT,
                PRIMARY KEY(run_id, role)
            );
            CREATE TABLE IF NOT EXISTS actions (
                run_id TEXT,
                action_id TEXT,
                status TEXT,
                result TEXT,
                PRIMARY KEY(run_id, action_id)
            );
            PRAGMA user_version=1;
        """
        self.db.executescript(schema_sql)

    def close(self):
        self.db.close()

    def submit(self, spec, key=None):
        spec = redact(spec)
        encoded = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        if key:
            row = self.db.execute("SELECT * FROM tasks WHERE submission_key=?", (key,)).fetchone()
            if row:
                if row["spec"] != encoded:
                    raise FrameworkError(ErrorCode.IDEMPOTENCY_CONFLICT)
                return row["id"]
        task_id = uid("task")
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?)", (task_id, key, encoded, now()))
        return task_id

    def task(self, task_id):
        row = self.db.execute("SELECT spec FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise FrameworkError(ErrorCode.NOT_FOUND, "Task not found")
        return json.loads(row[0])

    def create_run(self, task_id, mode, name=None):
        task = self.task(task_id)
        if name is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", name):
            raise FrameworkError(ErrorCode.INVALID_RUN_NAME)
        subject = name if name is not None else run_subject(task)
        label = re.sub(r"[^a-z0-9._-]+", "-", subject.lower())[:40].strip("._-") or "task"
        timestamp = now()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            sequence = self.db.execute("SELECT COALESCE(MAX(rowid),0)+1 FROM runs").fetchone()[0]
            run_id = f"run_{mode}_{label}_{sequence:06d}"
            self.db.execute(
                "INSERT INTO runs(rowid,id,task_id,mode,status,stage,cancel_requested,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?)",
                (sequence, run_id, task_id, mode, "queued", "intake", timestamp, timestamp),
            )
            self._event(run_id, "run.created", {"mode": mode, "name": label})
        self.run_dir(run_id).mkdir(parents=True)
        return run_id

    def list_runs(self, limit=20):
        if not 1 <= limit <= 200:
            raise FrameworkError(ErrorCode.INVALID_RUN_LIMIT)
        query = """
            SELECT r.id AS run_id,r.task_id,r.mode,r.status,r.stage,r.created_at,t.spec
            FROM runs r JOIN tasks t ON t.id=r.task_id
            ORDER BY r.rowid DESC LIMIT ?
        """
        rows = self.db.execute(query, (limit,)).fetchall()
        runs = []
        for row in rows:
            entry = dict(row)
            task = json.loads(entry.pop("spec"))
            entry["subject"] = run_subject(task)
            runs.append(entry)
        return runs

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise FrameworkError(ErrorCode.NOT_FOUND, "Run not found")
        run = dict(row)
        return run

    def run_dir(self, run_id):
        return within(self.var / "runs", run_id)

    def _event(
        self,
        run_id,
        kind,
        payload,
        span_id=None,
        parent_span_id=None,
        request_id=None,
        event_id=None,
    ):
        if not isinstance(payload, dict):
            raise FrameworkError(ErrorCode.INVALID_EVENT, "Event payload must be an object")
        run = self.run(run_id)
        event = {
            "event_id": event_id or uid("evt"),
            "task_id": run["task_id"],
            "run_id": run_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "request_id": request_id,
            "kind": kind,
            "timestamp": now(),
            "payload": redact(payload),
        }
        previous = self.db.execute(
            "SELECT data FROM events WHERE event_id=?", (event["event_id"],)
        ).fetchone()
        if previous:
            old = json.loads(previous[0])
            if any(old.get(key) != value for key, value in event.items() if key != "timestamp"):
                raise FrameworkError(ErrorCode.EVENT_ID_CONFLICT)
            return old
        self.db.execute(
            "INSERT OR IGNORE INTO events(event_id,run_id,span_id,kind,data) VALUES(?,?,?,?,?)",
            (event["event_id"], run_id, span_id, kind, json.dumps(event, ensure_ascii=False)),
        )
        return event

    def event(self, run_id, kind, payload, **ids):
        try:
            with self.db:
                return self._event(run_id, kind, payload, **ids)
        except sqlite3.Error as exc:
            raise FrameworkError(
                ErrorCode.TRACE_UNAVAILABLE, "Cannot persist trace; execution halted"
            ) from exc

    def transition(self, run_id, status, stage=None, reason=None):
        run = self.run(run_id)
        allowed = {
            "queued": {"running", "cancelled", "blocked"},
            "running": {"running", "blocked", "failed", "succeeded", "cancelled"},
            "blocked": {"running", "cancelled", "blocked"},
            "failed": set(),
            "succeeded": set(),
            "cancelled": set(),
        }
        if status not in allowed[run["status"]]:
            if status == run["status"]:
                return
            raise FrameworkError(
                ErrorCode.INVALID_STATE, f"Cannot change {run['status']} to {status}"
            )
        with self.db:
            state_payload = {
                "from": run["status"],
                "to": status,
                "stage": stage or run["stage"],
                "reason": reason,
            }
            self._event(run_id, "run.state", state_payload)
            self.db.execute(
                "UPDATE runs SET status=?,stage=?,updated_at=? WHERE id=?",
                (status, stage or run["stage"], now(), run_id),
            )

    def events(self, run_id, after=0, span=None):
        self.run(run_id)
        sql = "SELECT seq,data FROM events WHERE run_id=? AND seq>?"
        args = [run_id, after]
        if span:
            sql += " AND span_id=?"
            args.append(span)
        rows = self.db.execute(sql + " ORDER BY seq", args).fetchall()
        events = [{"seq": row[0], **json.loads(row[1])} for row in rows]
        return events

    def artifact(self, run_id, relative, value):
        path = within(self.run_dir(run_id), relative)
        atomic_json(path, redact(value))
        return path.relative_to(self.run_dir(run_id)).as_posix()

    def session(self, run_id, role):
        row = self.db.execute(
            "SELECT thread_id FROM sessions WHERE run_id=? AND role=?", (run_id, role)
        ).fetchone()
        return row[0] if row else None

    def save_session(self, run_id, role, thread_id):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (run_id, role, thread_id)
            )
            self._event(run_id, "ai.session", {"role": role, "thread_id": thread_id})

    def cancel(self, run_id):
        if self.run(run_id)["status"] in ("succeeded", "failed", "cancelled"):
            return
        with self.db:
            self.db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
            self._event(run_id, "run.cancel_requested", {})

    def supplement(self, run_id, text):
        if not text.strip() or "\x00" in text or len(text.encode("utf-8")) > 200_000:
            raise FrameworkError(
                ErrorCode.INVALID_DOCUMENT,
                "Supplement must contain 1 to 200,000 UTF-8 bytes and no null bytes",
            )
        try:
            with self.db:
                # Serialize the state check and append against a concurrent resume.
                self.db.execute("BEGIN IMMEDIATE")
                run = self.run(run_id)
                if run["mode"] == "preprocessing":
                    raise FrameworkError(ErrorCode.APK_INPUT_REQUIRED)
                if run["status"] != "blocked":
                    raise FrameworkError(ErrorCode.INVALID_STATE)
                if (
                    run["mode"] in ("intake", "game")
                    and self.db.execute(
                        "SELECT 1 FROM events WHERE run_id=? AND kind='intake.accepted'", (run_id,)
                    ).fetchone()
                ):
                    raise FrameworkError(
                        ErrorCode.INTAKE_NEW_RUN_REQUIRED,
                        "Intake is accepted; submit a new task to change requirements",
                    )
                return self._event(run_id, "task.supplement", {"text": text})
        except sqlite3.Error as exc:
            raise FrameworkError(
                ErrorCode.TRACE_UNAVAILABLE, "Cannot persist supplement; execution halted"
            ) from exc

    def unknown_actions(self, run_id):
        action_ids = [
            row[0]
            for row in self.db.execute(
                "SELECT action_id FROM actions WHERE run_id=? AND status='unknown'", (run_id,)
            )
        ]
        return action_ids
