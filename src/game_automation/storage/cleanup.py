"""Explicit task removal with a durable intent shared by SQLite and filesystem cleanup."""

import json
import shutil
from pathlib import Path

from game_automation.common import FileLock, FrameworkError, atomic_json, within
from game_automation.errors import ErrorCode


def checked_path(store, relative):
    parts = Path(relative).parts
    if len(parts) != 2 or parts[0] not in ("tasks", "runs") or parts[1] in (".", ".."):
        raise FrameworkError(ErrorCode.INVALID_PATH)
    base = store.var / parts[0]
    if base.is_symlink() or base.is_junction():
        raise FrameworkError(ErrorCode.INVALID_PATH)
    original = store.var / relative
    if original.is_symlink() or original.is_junction():
        raise FrameworkError(ErrorCode.INVALID_PATH)
    path = within(base, parts[1])
    if not path.is_relative_to(store.var.resolve()):
        raise FrameworkError(ErrorCode.INVALID_PATH)
    for child in path.rglob("*"):
        if child.is_symlink() or child.is_junction():
            raise FrameworkError(ErrorCode.INVALID_PATH)
        if not child.resolve().is_relative_to(path):
            raise FrameworkError(ErrorCode.INVALID_PATH)
    return path


def check_dependencies(store, task_ids):
    selected = set(task_ids)
    run_ids = {
        row["id"]
        for row in store.db.execute("SELECT id,task_id FROM runs")
        if row["task_id"] in selected
    }
    for row in store.db.execute(
        "SELECT r.task_id,i.spec FROM runs r JOIN run_inputs i ON i.run_id=r.id"
    ):
        if row["task_id"] in selected:
            continue
        spec = json.loads(row["spec"])
        if spec.get("source_run") in run_ids or spec.get("prepared_run") in run_ids:
            raise FrameworkError(ErrorCode.CLEANUP_DEPENDENCY)
    return run_ids


def plan_cleanup(store, task_ids, *, all_tasks, missing):
    roots = {
        row[0]
        for row in store.db.execute(
            "SELECT id FROM tasks WHERE id NOT IN (SELECT alias_id FROM task_aliases)"
        )
    }
    if all_tasks:
        selected = roots
    elif missing:
        selected = set()
        for task_id in roots:
            runs = store.task_runs(task_id)
            legacy_present = any((store.var / "runs" / run["run_id"]).exists() for run in runs)
            if not store.task_dir(task_id).exists() and not legacy_present:
                selected.add(task_id)
    elif task_ids:
        selected = {store.canonical_task_id(task_id) for task_id in task_ids}
        if not selected.issubset(roots):
            raise FrameworkError(ErrorCode.NOT_FOUND, "Cleanup task not found")
    else:
        raise FrameworkError(ErrorCode.INVALID_CONTRACT, "Select --task-id, --missing or --all")
    run_ids = check_dependencies(store, selected)
    aliases = {
        row["alias_id"]
        for row in store.db.execute("SELECT alias_id,task_id FROM task_aliases")
        if row["task_id"] in selected
    }
    paths = {f"tasks/{task_id}" for task_id in selected | aliases}
    paths.update(f"runs/{run_id}" for run_id in run_ids)
    if all_tasks:
        for name in ("tasks", "runs"):
            directory = store.var / name
            if directory.exists():
                paths.update(f"{name}/{path.name}" for path in directory.iterdir())
    file_bytes = 0
    for relative in paths:
        path = checked_path(store, relative)
        if path.is_file():
            file_bytes += path.stat().st_size
        else:
            file_bytes += sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
    plan = {
        "task_ids": sorted(selected),
        "execution_count": len(run_ids),
        "paths": sorted(paths),
        "file_bytes": file_bytes,
        "database_bytes_before": (store.var / "state.sqlite").stat().st_size,
        "preserved": ["secrets", "migrations", "shared resources", "other var files"],
    }
    return plan


def cleanup(store, task_ids=None, *, all_tasks=False, missing=False, apply=False):
    with FileLock(store.var / "execution.lock"), FileLock(store.var / "layout.lock"):
        if store.cleanup_journal.parent.is_symlink() or store.cleanup_journal.parent.is_junction():
            raise FrameworkError(ErrorCode.INVALID_PATH)
        pending = store.cleanup_journal.exists()
        if pending:
            if task_ids or all_tasks or missing:
                raise FrameworkError(
                    ErrorCode.CLEANUP_PENDING,
                    "Pending cleanup has a fixed scope; use cleanup --apply to finish it",
                )
            plan = json.loads(store.cleanup_journal.read_text(encoding="utf-8"))
        else:
            plan = plan_cleanup(store, task_ids, all_tasks=all_tasks, missing=missing)
        result = {"applied": False, "pending": pending, **plan}
        if not apply:
            return result
        if not pending:
            atomic_json(store.cleanup_journal, plan)
        # Filesystem deletion and a SQLite commit cannot be one atomic transaction.
        # Keep the fixed intent until both deletion and compaction finish; retries are idempotent.
        try:
            store._write("prepare_cleanup")
            check_dependencies(store, plan["task_ids"])
            for relative in plan["paths"]:
                path = checked_path(store, relative)
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            store._write("delete_tasks", task_ids=plan["task_ids"])
            store._write("compact")
        except FrameworkError as exc:
            raise FrameworkError(ErrorCode.CLEANUP_FAILED) from exc
        store.cleanup_journal.unlink()
        result.update(
            applied=True,
            pending=False,
            database_bytes_after=(store.var / "state.sqlite").stat().st_size,
        )
        return result
