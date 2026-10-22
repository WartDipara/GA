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
        if version not in (0, 1, 2, 3):
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
            CREATE TABLE IF NOT EXISTS device_bindings (
                instance_id TEXT PRIMARY KEY,
                apk_key TEXT UNIQUE NOT NULL,
                instance_index INTEGER NOT NULL,
                run_id TEXT UNIQUE NOT NULL REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS gameplay_progress (
                workspace_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id),
                data TEXT NOT NULL
            );
            PRAGMA user_version=3;
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

    def create_run(self, task_id, mode):
        task = self.task(task_id)
        subject = run_subject(task)
        label = re.sub(r"[^a-z0-9._-]+", "-", subject.lower())[:40].strip("._-") or "task"
        timestamp = now()
        (self.var / "runs").mkdir(parents=True, exist_ok=True)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for _ in range(10):
                run_id = uid(f"run_{label}")
                existing = self.db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone()
                if existing:
                    continue
                try:
                    self.run_dir(run_id).mkdir(exist_ok=False)
                except FileExistsError:
                    continue
                break
            else:
                raise FrameworkError(ErrorCode.RUN_ID_COLLISION)
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,0,?,?)",
                (run_id, task_id, mode, "queued", "intake", timestamp, timestamp),
            )
            self._event(run_id, "run.created", {"mode": mode, "name": label})
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
        try:
            atomic_json(path, redact(value))
        except OSError as exc:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE) from exc
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

    def reserve_device(self, run_id, instance_id, instance_index, apk_key):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.db.execute(
                "SELECT * FROM device_bindings WHERE instance_id=? OR apk_key=?",
                (instance_id, apk_key),
            ).fetchall()
            if rows:
                expected = {
                    "instance_id": instance_id,
                    "apk_key": apk_key,
                    "instance_index": instance_index,
                }
                if len(rows) != 1 or any(rows[0][key] != value for key, value in expected.items()):
                    raise FrameworkError(ErrorCode.DEVICE_BINDING_CONFLICT)
                if rows[0]["run_id"] != run_id:
                    payload = {"instance_id": instance_id, "reservation_run": rows[0]["run_id"]}
                    self._event(run_id, "device.reservation.reused", payload)
                return
            self.db.execute(
                "INSERT INTO device_bindings VALUES(?,?,?,?)",
                (instance_id, apk_key, instance_index, run_id),
            )
            binding = {
                "instance_id": instance_id,
                "instance_index": instance_index,
                "apk_key": apk_key,
            }
            self._event(run_id, "device.reserved", binding)

    def begin_device_action(
        self, run_id, instance_id, input_ref, max_actions, span_id, capability="device.install"
    ):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            binding = self.db.execute(
                "SELECT apk_key FROM device_bindings WHERE instance_id=?", (instance_id,)
            ).fetchone()
            task = self.task(self.run(run_id)["task_id"])
            apk_key = f"{task.get('source_run')}:{task.get('apk_index')}"
            if (
                binding is None
                or binding[0] != apk_key
                or task.get("kind") not in ("device_preparation", "gameplay")
            ):
                raise FrameworkError(ErrorCode.DEVICE_BINDING_CONFLICT)
            count = self.db.execute(
                "SELECT COUNT(*) FROM actions WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if max_actions is not None and count >= max_actions:
                raise FrameworkError(ErrorCode.BUDGET_EXHAUSTED)
            pending = self.db.execute(
                "SELECT 1 FROM actions WHERE json_extract(result,'$.instance_id')=? "
                "AND status IN ('prepared','unknown','executed')",
                (instance_id,),
            ).fetchone()
            if pending:
                code = (
                    ErrorCode.DEVICE_INSTALL_UNKNOWN
                    if capability == "device.install"
                    else ErrorCode.ACTION_ALREADY_DISPATCHED
                )
                raise FrameworkError(code)
            action_id = uid("action")
            details = {
                "capability": capability,
                "input_ref": input_ref,
                "instance_id": instance_id,
                "span_id": span_id,
            }
            self.db.execute(
                "INSERT INTO actions VALUES(?,?,?,?)",
                (run_id, action_id, "prepared", json.dumps(details)),
            )
            self._event(
                run_id, "action.intent", {"action_id": action_id, **details}, span_id=span_id
            )
        return action_id

    def dispatch_device_action(self, run_id, action_id):
        with self.db:
            row = self.db.execute(
                "SELECT status,result FROM actions WHERE run_id=? AND action_id=?",
                (run_id, action_id),
            ).fetchone()
            if row is None or row[0] != "prepared":
                raise FrameworkError(ErrorCode.INVALID_STATE)
            details = json.loads(row[1])
            self.db.execute(
                "UPDATE actions SET status='unknown' WHERE run_id=? AND action_id=?",
                (run_id, action_id),
            )
            self._event(
                run_id,
                "action.dispatched",
                {"action_id": action_id, **details},
                span_id=details["span_id"],
            )

    def latest_device_action(self, run_id):
        task = self.task(self.run(run_id)["task_id"])
        apk_key = f"{task.get('source_run')}:{task.get('apk_index')}"
        row = self.db.execute(
            "SELECT a.run_id,a.action_id,a.status,a.result FROM actions a "
            "JOIN device_bindings b ON json_extract(a.result,'$.instance_id')=b.instance_id "
            "WHERE b.apk_key=? AND (?='gameplay' OR "
            "json_extract(a.result,'$.capability')='device.install') "
            "ORDER BY a.rowid DESC LIMIT 1",
            (apk_key, task.get("kind")),
        ).fetchone()
        if row is None:
            return None
        action = {
            "run_id": row["run_id"],
            "action_id": row["action_id"],
            "status": row["status"],
            **json.loads(row["result"]),
        }
        return action

    def progress(self, workspace_key):
        row = self.db.execute(
            "SELECT run_id,data FROM gameplay_progress WHERE workspace_key=?", (workspace_key,)
        ).fetchone()
        if row is None:
            return None
        progress = {"run_id": row[0], "data": json.loads(row[1])}
        return progress

    def checkpoint(self, run_id, workspace_key, data):
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO gameplay_progress VALUES(?,?,?) "
                    "ON CONFLICT(workspace_key) DO UPDATE SET run_id=excluded.run_id,"
                    "data=excluded.data",
                    (workspace_key, run_id, json.dumps(redact(data))),
                )
                self._event(
                    run_id,
                    "gameplay.checkpoint",
                    {"workspace_key": workspace_key, "progress": data},
                )
        except sqlite3.Error as exc:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE) from exc

    def finish_device_action(self, run_id, action_id, status, result):
        if status not in ("executed", "rejected", "verified", "reconciled"):
            raise FrameworkError(ErrorCode.INVALID_STATE)
        with self.db:
            row = self.db.execute(
                "SELECT status,result FROM actions WHERE run_id=? AND action_id=?",
                (run_id, action_id),
            ).fetchone()
            if row is None or row[0] not in ("prepared", "unknown", "executed"):
                raise FrameworkError(ErrorCode.INVALID_STATE)
            if row[0] == "prepared" and status != "rejected":
                raise FrameworkError(ErrorCode.INVALID_STATE)
            details = {**json.loads(row[1]), "result": redact(result)}
            self.db.execute(
                "UPDATE actions SET status=?,result=? WHERE run_id=? AND action_id=?",
                (status, json.dumps(details), run_id, action_id),
            )
            payload = {"action_id": action_id, "from": row[0], "status": status, **details}
            self._event(run_id, "action.result", payload, span_id=details["span_id"])
