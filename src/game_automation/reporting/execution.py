"""Read-only outcome snapshot and publication-status projections."""

from game_automation.common import FrameworkError, now, within

SETTLED_STATUSES = ("succeeded", "failed", "cancelled", "blocked")


def snapshot_metadata(store, run_id):
    run = store.run(run_id)
    states = store.events(run_id, kinds=("run.state",), limit=1)
    latest = store.db.execute("SELECT MAX(seq) FROM events WHERE run_id=?", (run_id,)).fetchone()
    snapshot = {
        "generated_at": now(),
        "business_status": run["status"],
        "stage": run["stage"],
        "state_event_id": states[0]["event_id"] if states else None,
        "source_event_seq": latest[0],
        "kind": "final"
        if run["status"] in ("succeeded", "failed", "cancelled")
        else "checkpoint"
        if run["status"] == "blocked"
        else "in_progress",
    }
    return snapshot


def publication_status(store, run_id):
    requests = store.events(run_id, kinds=("report.requested",), limit=1)
    finished = store.events(run_id, kinds=("report.finished",), limit=1)
    if not requests:
        result = {"status": "untracked", "request_id": None}
    elif (
        not finished or finished[0]["payload"]["request_id"] != requests[0]["payload"]["request_id"]
    ):
        result = {"status": "pending", **requests[0]["payload"]}
    else:
        result = dict(finished[0]["payload"])
        if result["status"] == "complete":
            references = [
                "reports/publication.json",
                *(item["ref"] for item in result.get("files", [])),
            ]
            try:
                missing = [
                    reference
                    for reference in references
                    if not within(store.run_dir(run_id), reference).is_file()
                ]
                if missing:
                    result["status"] = "missing_files"
                    result["missing_files"] = missing
                changed = [
                    item["ref"]
                    for item in result.get("files", [])
                    if item["ref"] not in missing
                    and within(store.run_dir(run_id), item["ref"]).stat().st_size != item["bytes"]
                ]
                if changed:
                    result["status"] = "changed_files"
                    result["changed_files"] = changed
                shared = ("reports/task.json", "reports/task.html", "apks/index.json")
                task_directory = store.task_dir(store.run(run_id)["task_id"])
                missing_shared = [
                    reference for reference in shared if not (task_directory / reference).is_file()
                ]
                if missing_shared:
                    result["status"] = "missing_files"
                    result["missing_task_files"] = missing_shared
            except (FrameworkError, OSError):
                result["status"] = "unavailable"
    if store.run(run_id)["status"] not in SETTLED_STATUSES:
        result["status"] = "in_progress"
    return result
