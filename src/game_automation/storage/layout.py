"""Upgrade task ownership without rewriting historical events or execution inputs."""

import json
import sqlite3

from game_automation.common import FileLock, FrameworkError, atomic_json, within
from game_automation.errors import ErrorCode


def upgrade(db, directory, version):
    if version == 4:
        return
    if version:
        backup = directory / "migrations/task-layout/state-before-upgrade.sqlite"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            with sqlite3.connect(backup) as target:
                db.backup(target)
    tasks = {row["id"]: json.loads(row["spec"]) for row in db.execute("SELECT id,spec FROM tasks")}
    owners = {row["id"]: row["task_id"] for row in db.execute("SELECT id,task_id FROM runs")}
    parents = {}
    for task_id, spec in tasks.items():
        parent_run = spec.get("prepared_run") or spec.get("source_run")
        if parent_run in owners and owners[parent_run] != task_id:
            parents[task_id] = owners[parent_run]

    def root(task_id):
        visited = set()
        while task_id in parents:
            if task_id in visited:
                raise FrameworkError(ErrorCode.INVALID_STATE, "Task lineage contains a cycle")
            visited.add(task_id)
            task_id = parents[task_id]
        return task_id

    with db:
        for run_id, task_id in owners.items():
            db.execute(
                "INSERT INTO run_inputs VALUES(?,?)",
                (run_id, json.dumps(tasks[task_id], ensure_ascii=False)),
            )
            db.execute("UPDATE runs SET task_id=? WHERE id=?", (root(task_id), run_id))
        for task_id in parents:
            db.execute("INSERT INTO task_aliases VALUES(?,?)", (task_id, root(task_id)))
        rows = db.execute("SELECT workspace_key,run_id FROM gameplay_progress").fetchall()
        for row in rows:
            task_id = root(owners[row["run_id"]])
            db.execute(
                "UPDATE gameplay_progress SET workspace_key=? WHERE workspace_key=?",
                (f"{task_id}:{row['workspace_key']}", row["workspace_key"]),
            )
        db.execute("PRAGMA user_version=4")


def relocate(store):
    """Move registered directories on the same volume; interruption is safe to resume."""
    from game_automation.reporting.task_index import task_report

    with FileLock(store.var / "execution.lock"), FileLock(store.var / "layout.lock"):
        store.check_cleanup()
        legacy = store.var / "runs"
        tasks = store.var / "tasks"
        plan = []
        for row in store.db.execute("SELECT id,task_id FROM runs ORDER BY rowid"):
            source = within(legacy, row["id"])
            target = within(store.task_dir(row["task_id"]) / "executions", row["id"])
            if source.exists() and target.exists():
                raise FrameworkError(ErrorCode.WORKSPACE_CONFLICT)
            if source.is_symlink() or source.is_junction():
                raise FrameworkError(ErrorCode.WORKSPACE_CONFLICT)
            if not source.resolve().is_relative_to(legacy.resolve()):
                raise FrameworkError(ErrorCode.WORKSPACE_CONFLICT)
            if not target.resolve().is_relative_to(tasks.resolve()):
                raise FrameworkError(ErrorCode.WORKSPACE_CONFLICT)
            entry = {
                "run_id": row["id"],
                "task_id": row["task_id"],
                "source": source.relative_to(store.var).as_posix(),
                "target": target.relative_to(store.var).as_posix(),
                "missing": not source.exists() and not target.exists(),
            }
            plan.append(entry)
        journal = store.var / "migrations/task-layout/relocations.json"
        atomic_json(journal, plan)
        moved = 0
        for entry in plan:
            source = within(store.var, entry["source"])
            target = within(store.var, entry["target"])
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                moved += 1
                payload = {"source": entry["source"], "target": entry["target"]}
                store.event(entry["run_id"], "workspace.relocated", payload)
        task_ids = {
            row[0]
            for row in store.db.execute(
                "SELECT id FROM tasks WHERE id NOT IN (SELECT alias_id FROM task_aliases)"
            )
        }
        for task_id in task_ids:
            task_report(store, task_id, layout_locked=True)
        if legacy.exists() and not any(legacy.iterdir()):
            legacy.rmdir()
        result = {
            "moved_executions": moved,
            "tasks": len(task_ids),
            "missing_historical_executions": sum(entry["missing"] for entry in plan),
            "unregistered_directories": len(list(legacy.iterdir())) if legacy.exists() else 0,
            "journal": journal.relative_to(store.var).as_posix(),
        }
        return result
