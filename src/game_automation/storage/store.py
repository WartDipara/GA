from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from game_automation.common import FileLock, FrameworkError, atomic_json, redact, within
from game_automation.errors import ErrorCode

from .control_client import ControlClient
from .usage import FileUsage


def run_subject(task):
    if task.get("kind") in ("apk_batch", "apk_workflow"):
        return f"{len(task['source_ids'])}-apks"
    if task.get("kind") == "document":
        return Path(task["filename"]).stem
    return task.get("target") or "task"


class Store:
    """Read-only projections with writes delegated to the TypeScript kernel."""

    @classmethod
    def open_readonly(cls, root: Path):
        store = cls.__new__(cls)
        store.root = root.resolve()
        store.var = store.root / "var"
        store.cleanup_journal = store.var / "cleanup/pending.json"
        store.check_cleanup()
        database_uri = (store.var / "state.sqlite").as_uri() + "?mode=ro"
        store.writer = None
        store.file_usage = {}
        try:
            store.db = sqlite3.connect(database_uri, uri=True, timeout=2)
            store.db.row_factory = sqlite3.Row
            store.db.execute("PRAGMA query_only=ON")
            store.has_event_usage = bool(
                store.db.execute("SELECT 1 FROM sqlite_master WHERE name='event_usage'").fetchone()
            )
        except sqlite3.Error as exc:
            if hasattr(store, "db"):
                store.db.close()
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE) from exc
        return store

    def __init__(self, root: Path, *, allow_cleanup=False):
        self.root = root.resolve()
        self.var = self.root / "var"
        self.var.mkdir(parents=True, exist_ok=True)
        self.cleanup_journal = self.var / "cleanup/pending.json"
        if not allow_cleanup:
            self.check_cleanup()
        with FileLock(self.var / "layout.lock"):
            if not allow_cleanup:
                self.check_cleanup()
            self.writer = ControlClient.start(self.root)
        database_uri = (self.var / "state.sqlite").as_uri() + "?mode=ro"
        try:
            self.db = sqlite3.connect(database_uri, uri=True, timeout=2)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
        except BaseException:
            self.writer.close()
            raise
        self.file_usage = {}
        self.has_event_usage = True

    def _write(self, method, **params):
        if self.writer is None:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED, "This store is read-only")
        return self.writer.request("storage." + method, params)

    def close(self):
        self.db.close()
        if self.writer is not None:
            self.writer.close()

    def quiesce(self):
        """Caller owns execution.lock; drain previous calls before reconciling their evidence."""
        if self.writer is None:
            raise FrameworkError(ErrorCode.CAPABILITY_DENIED)
        return self.writer.request("storage.quiesce", {}, timeout=None)

    def check_cleanup(self):
        if self.cleanup_journal.exists():
            raise FrameworkError(ErrorCode.CLEANUP_PENDING)

    def submit(self, spec, key=None):
        with FileLock(self.var / "layout.lock"):
            self.check_cleanup()
            return self._write("submit", spec=spec, key=key)

    def canonical_task_id(self, task_id):
        row = self.db.execute(
            "SELECT task_id FROM task_aliases WHERE alias_id=?", (task_id,)
        ).fetchone()
        return row[0] if row else task_id

    def task_dir(self, task_id):
        return within(self.var / "tasks", self.canonical_task_id(task_id))

    def write_task(self, task_id):
        task_id = self.canonical_task_id(task_id)
        details = {"task_id": task_id, "submission": self.task(task_id)}
        atomic_json(self.task_dir(task_id) / "task.json", details)

    def task(self, task_id):
        row = self.db.execute("SELECT spec FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise FrameworkError(ErrorCode.NOT_FOUND, "Task not found")
        return json.loads(row[0])

    def run_task(self, run_id):
        row = self.db.execute("SELECT spec FROM run_inputs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise FrameworkError(ErrorCode.NOT_FOUND, "Execution input not found")
        return json.loads(row[0])

    def create_run(self, task_id, mode, spec=None):
        with FileLock(self.var / "layout.lock"):
            self.check_cleanup()
            return self._write("create_run", task_id=task_id, mode=mode, spec=spec)

    def list_runs(self, limit=20):
        if not 1 <= limit <= 200:
            raise FrameworkError(ErrorCode.INVALID_RUN_LIMIT)
        query = """
            SELECT r.id AS run_id,r.task_id,r.mode,r.status,r.stage,r.created_at,i.spec
            FROM runs r JOIN run_inputs i ON i.run_id=r.id
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

    def list_tasks(self, limit=20):
        if not 1 <= limit <= 200:
            raise FrameworkError(ErrorCode.INVALID_RUN_LIMIT)
        rows = self.db.execute(
            "SELECT t.id AS task_id,t.spec,COUNT(r.id) AS executions "
            "FROM tasks t LEFT JOIN runs r ON r.task_id=t.id "
            "WHERE NOT EXISTS (SELECT 1 FROM task_aliases a WHERE a.alias_id=t.id) "
            "GROUP BY t.id ORDER BY MAX(r.rowid) DESC,t.rowid DESC LIMIT ?",
            (limit,),
        )
        tasks = []
        for row in rows:
            item = dict(row)
            item["subject"] = run_subject(json.loads(item.pop("spec")))
            item["directory"] = str(self.task_dir(item["task_id"]))
            tasks.append(item)
        return tasks

    def task_runs(self, task_id):
        task_id = self.canonical_task_id(task_id)
        self.task(task_id)
        rows = self.db.execute(
            "SELECT id AS run_id,mode,status,stage FROM runs WHERE task_id=? ORDER BY rowid",
            (task_id,),
        )
        runs = [dict(row) for row in rows]
        return runs

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise FrameworkError(ErrorCode.NOT_FOUND, "Run not found")
        run = dict(row)
        return run

    def run_dir(self, run_id):
        task_id = self.run(run_id)["task_id"]
        directory = within(self.task_dir(task_id) / "executions", run_id)
        legacy = within(self.var / "runs", run_id)
        if directory.exists() and legacy.exists():
            raise FrameworkError(ErrorCode.WORKSPACE_CONFLICT)
        return legacy if legacy.exists() else directory

    def artifact_path(self, run_id, path):
        path = Path(path)
        legacy = within(self.var / "runs", run_id)
        if path.is_relative_to(legacy):
            path = path.relative_to(legacy)
        return within(self.run_dir(run_id), path)

    def event(self, run_id, kind, payload, **ids):
        return self._write("event", run_id=run_id, kind=kind, payload=payload, **ids)

    def event_batch(self, run_id, entries, **ids):
        self._write("event_batch", run_id=run_id, entries=entries, **ids)

    def transition(self, run_id, status, stage=None, reason=None):
        self._write("transition", run_id=run_id, status=status, stage=stage, reason=reason)

    def iter_events(self, run_id, after=0, span=None, *, kinds=None, limit=None, through=None):
        self.run(run_id)
        sql = "SELECT seq,data FROM events WHERE run_id=? AND seq>?"
        args = [run_id, after]
        if through is not None:
            sql += " AND seq<=?"
            args.append(through)
        if span:
            sql += " AND span_id=?"
            args.append(span)
        if kinds:
            sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            args.extend(kinds)
        sql += " ORDER BY seq DESC" if limit else " ORDER BY seq"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        for row in self.db.execute(sql, args):
            event = {"seq": row[0], **json.loads(row[1])}
            if event["payload"].get("event_payloads_referenced"):
                message = event["payload"]["message"]
                references = message["params"]["events"]
                expanded = []
                for reference in references:
                    original = self.db.execute(
                        "SELECT data FROM events WHERE event_id=? AND run_id=?",
                        (reference["event_id"], run_id),
                    ).fetchone()
                    if original:
                        original = json.loads(original[0])
                        expanded.append(
                            {key: original[key] for key in ("event_id", "kind", "payload")}
                        )
                    else:
                        expanded.append({**reference, "unavailable": True})
                message["params"]["events"] = expanded
            yield event

    def events(self, run_id, after=0, span=None, *, kinds=None, limit=None):
        events = list(self.iter_events(run_id, after, span, kinds=kinds, limit=limit))
        if limit:
            events.reverse()
        return events

    def event_bytes(self, run_id):
        if self.has_event_usage:
            row = self.db.execute(
                "SELECT size_bytes FROM event_usage WHERE run_id=?", (run_id,)
            ).fetchone()
            return row[0] if row else 0
        row = self.db.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(data AS BLOB))),0) FROM events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return row[0]

    def files(self, run_id):
        if run_id not in self.file_usage:
            self.file_usage[run_id] = FileUsage(self.run_dir(run_id))
            self.file_usage[run_id].scan()
        return self.file_usage[run_id]

    def action_records(self, run_id):
        rows = self.db.execute(
            "SELECT action_id,status,result FROM actions WHERE run_id=? ORDER BY rowid", (run_id,)
        )
        actions = [{"action_id": row[0], "status": row[1], **json.loads(row[2])} for row in rows]
        return actions

    def artifact(self, run_id, relative, value):
        path = within(self.run_dir(run_id), relative)
        try:
            atomic_json(path, redact(value))
            if run_id in self.file_usage:
                self.file_usage[run_id].track(path)
        except OSError as exc:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE) from exc
        return path.relative_to(self.run_dir(run_id)).as_posix()

    def session(self, run_id, role):
        row = self.db.execute(
            "SELECT thread_id FROM sessions WHERE run_id=? AND role=?", (run_id, role)
        ).fetchone()
        return row[0] if row else None

    def save_session(self, run_id, role, thread_id):
        self._write("save_session", run_id=run_id, role=role, thread_id=thread_id)

    def cancel(self, run_id):
        self._write("cancel", run_id=run_id)

    def supplement(self, run_id, text):
        return self._write("supplement", run_id=run_id, text=text)

    def task_supplements(self, task_id):
        rows = self.db.execute(
            "SELECT e.data FROM events e JOIN runs r ON r.id=e.run_id "
            "WHERE r.task_id=? AND e.kind='task.supplement' ORDER BY e.seq",
            (task_id,),
        )
        supplements = [json.loads(row["data"]) for row in rows]
        return supplements

    def unknown_actions(self, run_id):
        action_ids = [
            row[0]
            for row in self.db.execute(
                "SELECT action_id FROM actions WHERE run_id=? AND status='unknown'", (run_id,)
            )
        ]
        return action_ids

    def reserve_device(self, run_id, instance_id, instance_index, apk_key):
        self._write(
            "reserve_device",
            run_id=run_id,
            instance_id=instance_id,
            instance_index=instance_index,
            apk_key=apk_key,
        )

    def begin_device_action(
        self, run_id, instance_id, input_ref, max_actions, span_id, capability="device.install"
    ):
        return self._write(
            "begin_device_action",
            run_id=run_id,
            instance_id=instance_id,
            input_ref=input_ref,
            max_actions=max_actions,
            span_id=span_id,
            capability=capability,
        )

    def dispatch_device_action(self, run_id, action_id):
        self._write("dispatch_device_action", run_id=run_id, action_id=action_id)

    def latest_device_action(self, run_id):
        task = self.run_task(run_id)
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
        self._write("checkpoint", run_id=run_id, workspace_key=workspace_key, data=data)

    def finish_device_action(self, run_id, action_id, status, result):
        self._write(
            "finish_device_action", run_id=run_id, action_id=action_id, status=status, result=result
        )
