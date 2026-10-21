from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from game_automation.common import FrameworkError, atomic_json, now, redact, uid, within


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
            raise FrameworkError("SCHEMA_VERSION", "Unsupported database version")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, submission_key TEXT UNIQUE, spec TEXT NOT NULL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), mode TEXT NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, cancel_requested INTEGER DEFAULT 0,
          created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          run_id TEXT REFERENCES runs(id), span_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS event_run ON events(run_id,seq);
        CREATE TABLE IF NOT EXISTS sessions(
          run_id TEXT, role TEXT, thread_id TEXT, PRIMARY KEY(run_id,role));
        CREATE TABLE IF NOT EXISTS actions(
          run_id TEXT, action_id TEXT, status TEXT, result TEXT,
          PRIMARY KEY(run_id,action_id));
        PRAGMA user_version=1;
        """)

    def close(self):
        self.db.close()

    def submit(self, spec, key=None):
        spec = redact(spec)
        encoded = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        if key:
            row = self.db.execute("SELECT * FROM tasks WHERE submission_key=?", (key,)).fetchone()
            if row:
                if row["spec"] != encoded:
                    raise FrameworkError(
                        "IDEMPOTENCY_CONFLICT", "Submission key belongs to another task"
                    )
                return row["id"]
        task_id = uid("task")
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?)", (task_id, key, encoded, now()))
        return task_id

    def task(self, task_id):
        row = self.db.execute("SELECT spec FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise FrameworkError("NOT_FOUND", "Task not found")
        return json.loads(row[0])

    def create_run(self, task_id, mode):
        self.task(task_id)
        run_id, timestamp = uid("run"), now()
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,0,?,?)",
                (run_id, task_id, mode, "queued", "intake", timestamp, timestamp),
            )
            self._event(run_id, "run.created", {"mode": mode})
        self.run_dir(run_id).mkdir(parents=True)
        return run_id

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise FrameworkError("NOT_FOUND", "Run not found")
        return dict(row)

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
            raise FrameworkError("INVALID_EVENT", "Event payload must be an object")
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
                raise FrameworkError(
                    "EVENT_ID_CONFLICT", "Event ID was reused for different content"
                )
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
                "TRACE_UNAVAILABLE", "Cannot persist trace; execution halted"
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
            raise FrameworkError("INVALID_STATE", f"Cannot change {run['status']} to {status}")
        with self.db:
            self._event(
                run_id,
                "run.state",
                {
                    "from": run["status"],
                    "to": status,
                    "stage": stage or run["stage"],
                    "reason": reason,
                },
            )
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
        return [{"seq": row[0], **json.loads(row[1])} for row in rows]

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
                "INVALID_DOCUMENT",
                "Supplement must contain 1 to 200,000 UTF-8 bytes and no null bytes",
            )
        try:
            with self.db:
                # Serialize the state check and append against a concurrent resume.
                self.db.execute("BEGIN IMMEDIATE")
                run = self.run(run_id)
                if run["status"] != "blocked":
                    raise FrameworkError("INVALID_STATE", "Supplement input only on blocked runs")
                if (
                    run["mode"] in ("intake", "game")
                    and self.db.execute(
                        "SELECT 1 FROM events WHERE run_id=? AND kind='intake.accepted'", (run_id,)
                    ).fetchone()
                ):
                    raise FrameworkError(
                        "INTAKE_NEW_RUN_REQUIRED",
                        "Intake is accepted; submit a new task to change requirements",
                    )
                return self._event(run_id, "task.supplement", {"text": text})
        except sqlite3.Error as exc:
            raise FrameworkError(
                "TRACE_UNAVAILABLE", "Cannot persist supplement; execution halted"
            ) from exc

    def reserve_action(self, run_id, action_id):
        # Never replay an already-dispatched action, including an unknown result.
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM actions WHERE run_id=? AND action_id=?", (run_id, action_id)
            ).fetchone():
                raise FrameworkError(
                    "ACTION_ALREADY_DISPATCHED", "Action requires reconciliation; not replayed"
                )
            self._event(run_id, "action.intent", {"action_id": action_id})
            self.db.execute(
                "INSERT INTO actions VALUES(?,?,?,NULL)", (run_id, action_id, "unknown")
            )

    def finish_action(self, run_id, action_id, result):
        with self.db:
            self._event(run_id, "action.result", result)
            self.db.execute(
                "UPDATE actions SET status=?,result=? WHERE run_id=? AND action_id=?",
                (result["status"], json.dumps(redact(result)), run_id, action_id),
            )

    def unknown_actions(self, run_id):
        return [
            r[0]
            for r in self.db.execute(
                "SELECT action_id FROM actions WHERE run_id=? AND status='unknown'", (run_id,)
            )
        ]
