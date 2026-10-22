"""Task-level indexes of existing executions; no model or device access."""

import html
from collections import Counter

from game_automation.common import atomic_json


def task_report(store, task_id):
    from game_automation.kernel.workflow.preprocessing import preprocessing_status

    task_id = store.canonical_task_id(task_id)
    store.write_task(task_id)
    directory = store.task_dir(task_id)
    executions = store.task_runs(task_id)
    apks = []
    rows = []
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
        label = html.escape(run_id)
        if execution["trace_available"]:
            label = f'<a href="../executions/{label}/reports/trace.html">{label}</a>'
        rows.append(
            f"<tr><td>{label}</td><td>{html.escape(execution['mode'])}</td>"
            f"<td>{html.escape(execution['status'])}</td>"
            f"<td>{execution['evidence_available']}</td></tr>"
        )
    report = {
        "task_id": task_id,
        "execution_statuses": dict(Counter(item["status"] for item in executions)),
        "executions": executions,
        "apks": apks,
        "scope": "Execution states do not establish completion or coverage of the whole task",
    }
    atomic_json(directory / "reports/task.json", report)
    atomic_json(directory / "apks/index.json", apks)
    document = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\"><title>Task executions</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px}"
        "td,th{text-align:left;padding:8px;overflow-wrap:anywhere}</style>"
        f"<h1>Task executions</h1><p>{html.escape(task_id)}</p>"
        "<p>Each row is a preserved stage execution. Missing historical evidence stays missing. "
        "Execution states do not establish whole-task completion or game coverage.</p>"
        '<p><a href="task.json">Task index</a> · <a href="../apks/index.json">APK index</a></p>'
        "<table><thead><tr><th>Execution</th><th>Mode</th><th>Status</th>"
        "<th>Evidence available</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    (directory / "reports/task.html").write_text(document, encoding="utf-8", newline="\n")
    return report
