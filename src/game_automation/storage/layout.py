"""Upgrade task ownership without rewriting historical events or execution inputs."""

from game_automation.common import FileLock, FrameworkError, atomic_json, within
from game_automation.errors import ErrorCode


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
