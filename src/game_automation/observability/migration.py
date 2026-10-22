"""Read-only comparison of evidence batches; equality of code is not game acceptance."""

import json
from pathlib import Path

from game_automation.common import FrameworkError, digest
from game_automation.errors import ErrorCode


def batch(store, run_id):
    run = store.run(run_id)
    task = store.run_task(run_id)
    path = store.run_dir(run_id) / "manifest.json"
    snapshots = store.events(run_id, kinds=("run.snapshot",), limit=1)
    if not snapshots or digest(path.read_bytes()) != snapshots[0]["payload"]["manifest_sha256"]:
        raise FrameworkError(ErrorCode.SNAPSHOT_CHANGED)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    sources = {
        Path(name).relative_to(store.root).as_posix(): value
        for name, value in manifest["source_hashes"].items()
    }
    completed = store.events(run_id, kinds=("gameplay.goal.completed",))
    result = {
        "run_id": run_id,
        "package_name": task.get("metadata", {}).get("packageName"),
        "status": run["status"],
        "mode": run["mode"],
        "goal": task.get("goal"),
        "verified_goals": [item["payload"] for item in completed],
        "sources": sources,
    }
    return result


def compare_games(store, first_run, second_run):
    first = batch(store, first_run)
    second = batch(store, second_run)
    same_sources = first.pop("sources") == second.pop("sources")
    different_packages = (
        first["package_name"] is not None
        and second["package_name"] is not None
        and first["package_name"] != second["package_name"]
    )
    verified = (
        different_packages
        and same_sources
        and first["verified_goals"]
        and second["verified_goals"]
        and first["status"] == second["status"] == "succeeded"
    )
    report = {
        "batches": [first, second],
        "same_runtime_sources": same_sources,
        "different_packages": different_packages,
        "migration_verified": bool(verified),
        "coverage": "Two bounded tasks only; no whole-game coverage claim",
    }
    store.artifact(second_run, "reports/migration.json", report)
    return report
