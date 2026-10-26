"""Task-level indexes of existing executions; no model or device access."""

from collections import Counter

from game_automation.common import FileLock, atomic_json, atomic_text

from .history import task_document


def task_report(store, task_id, *, layout_locked=False):
    if layout_locked:
        return write_task_report(store, task_id)
    with FileLock(store.var / "layout.lock"):
        store.check_cleanup()
        return write_task_report(store, task_id)


def write_task_report(store, task_id):
    from game_automation.kernel.workflow.preprocessing import preprocessing_status

    task_id = store.canonical_task_id(task_id)
    store.write_task(task_id)
    directory = store.task_dir(task_id)
    executions = store.task_runs(task_id)
    apks = []
    for execution in executions:
        run_id = execution["run_id"]
        execution["directory"] = f"executions/{run_id}"
        execution["evidence_available"] = store.run_dir(run_id).is_dir()
        report = store.run_dir(run_id) / "reports/trace.html"
        execution["trace_available"] = report.is_file()
        if execution["mode"] == "preprocessing":
            for item in preprocessing_status(store, run_id):
                entry = {
                    "run_id": run_id,
                    "index": item["index"],
                    "status": item["status"],
                    "metadata": item.get("metadata"),
                    "apk_ref": f"executions/{run_id}/{item['apk_ref']}"
                    if item.get("apk_ref")
                    else None,
                }
                apks.append(entry)
    report = {
        "task_id": task_id,
        "execution_statuses": dict(Counter(item["status"] for item in executions)),
        "executions": executions,
        "apks": apks,
        "scope": "Execution states do not establish completion or coverage of the whole task",
    }
    atomic_json(directory / "reports/task.json", report)
    atomic_json(directory / "apks/index.json", apks)
    document = task_document(store, task_id)
    atomic_text(directory / "reports/task.html", document)
    return report
