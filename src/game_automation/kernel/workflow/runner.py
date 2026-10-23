"""Compatibility entry points; execution, recovery and scheduling belong to TypeScript."""

from game_automation.common import FileLock

from .execution_client import invoke, invoke_async


def new_run(store, task_id, mode="game", spec=None):
    with FileLock(store.var / "execution.lock"):
        return invoke(store.root, "create", task_id=task_id, mode=mode, spec=spec)


async def execute(store, run_id, source_file=None):
    with FileLock(store.var / "execution.lock"):
        source = str(source_file.resolve()) if source_file is not None else None
        return await invoke_async(store.root, "run", run_id=run_id, source_file=source)
