"""Minimal outcome report and publication status, independent of rich evidence renderers."""

import html

from game_automation.common import FrameworkError, atomic_json, atomic_text, json_text, now, within

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


def write_execution_report(store, run_id, snapshot):
    run = store.run(run_id)
    states = store.events(run_id, kinds=("run.state",), limit=1)
    failures = store.events(run_id, kinds=("call.failed", "call.incomplete"), limit=20)
    report = {
        "run_id": run_id,
        "task_id": run["task_id"],
        "mode": run["mode"],
        "snapshot": snapshot,
        "reason": states[0]["payload"].get("reason") if states else None,
        "recent_call_failures": failures,
        "unresolved_actions": [
            {"action_id": action["action_id"], "status": action["status"]}
            for action in store.action_records(run_id)
            if action["status"] in ("prepared", "unknown", "executed")
        ],
        "scope": "Recorded outcome only; absent evidence does not establish success or coverage",
    }
    directory = store.run_dir(run_id) / "reports"
    atomic_json(directory / "execution.json", report)
    escaped = html.escape(json_text(report))
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\"><title>Execution outcome</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        f"<h1>Execution outcome: {html.escape(run['status'])}</h1>"
        "<p>This is recorded evidence, including failures and interruptions. "
        "Rich report failures do not change the business outcome.</p>"
        '<p><a href="trace.html">Call trace</a> · <a href="execution.json">Outcome JSON</a> · '
        '<a href="publication.json">Report publication status</a></p>'
        f"<pre>{escaped}</pre></html>"
    )
    atomic_text(directory / "execution.html", document)
