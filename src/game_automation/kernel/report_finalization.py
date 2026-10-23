"""Recoverable report publication after the durable business outcome; no device operations."""

import time
import traceback
from pathlib import Path

from game_automation.common import FrameworkError, atomic_json, atomic_text, uid
from game_automation.errors import ErrorCode
from game_automation.reporting.compact_trace import render_compact
from game_automation.reporting.execution import (
    SETTLED_STATUSES,
    publication_status,
    snapshot_metadata,
    write_execution_report,
)
from game_automation.reporting.gameplay import gameplay_report
from game_automation.reporting.task_index import task_report
from game_automation.reporting.trace import export_agent_trace, export_context


def report_error(exc):
    code = exc.code if isinstance(exc, FrameworkError) else ErrorCode.REPORT_GENERATION_FAILED
    error = {
        "code": code,
        "exception_type": type(exc).__name__,
        "frames": [
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        ],
    }
    return error


def ensure_report_request(store, run_id):
    states = store.events(run_id, kinds=("run.state",), limit=1)
    requests = store.events(run_id, kinds=("report.requested",), limit=1)
    state_id = states[0]["event_id"] if states else None
    if requests and requests[0]["payload"]["state_event_id"] == state_id:
        return requests[0]["payload"]
    request = {
        "request_id": uid("report"),
        "state_event_id": state_id,
        "business_status": store.run(run_id)["status"],
    }
    store.event(run_id, "report.requested", request)
    return request


def finalize_reports(store, run_id):
    """Caller owns execution.lock. A failed renderer must not fail a successful business stage."""
    run = store.run(run_id)
    if run["status"] not in SETTLED_STATUSES:
        raise FrameworkError(ErrorCode.REPORT_NOT_READY)
    request = ensure_report_request(store, run_id)
    snapshot = snapshot_metadata(store, run_id)
    publication_id = uid("publication")
    started = time.monotonic()
    start = {**request, "publication_id": publication_id, "snapshot": snapshot}
    store.event(run_id, "report.started", start)
    components = [
        ("outcome", lambda: write_execution_report(store, run_id, snapshot)),
    ]
    if run["mode"] == "gameplay":
        components.append(("gameplay", lambda: gameplay_report(store, run_id)))
    if run["mode"] == "workflow":
        components.append(("workflow", lambda: write_workflow_report(store, run_id)))
    components.extend(
        [
            ("trace", lambda: export_agent_trace(store, run_id)),
            ("context", lambda: export_context(store, run_id)),
            ("task_index", lambda: task_report(store, run["task_id"])),
        ]
    )
    outcomes = {}
    for name, render in components:
        step_started = time.monotonic()
        boundary = {"publication_id": publication_id, "component": name}
        store.event(run_id, "report.component.started", boundary)
        try:
            render()
            result = {"status": "complete"}
        except Exception as exc:
            result = {"status": "failed", "error": report_error(exc)}
            if name == "trace":
                try:
                    target = store.run_dir(run_id) / "reports/trace.html"
                    atomic_text(target, render_compact(store, run_id, degraded=True))
                    result["fallback"] = "reports/trace.html"
                except Exception as fallback_error:
                    result["fallback_error"] = report_error(fallback_error)
        result["duration_ms"] = round((time.monotonic() - step_started) * 1000)
        outcomes[name] = result
        store.event(run_id, "report.component.finished", {**boundary, **result})
    receipt = {
        **start,
        "status": "complete"
        if all(result["status"] == "complete" for result in outcomes.values())
        else "degraded",
        "components": outcomes,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    names = ["execution.json", "execution.html", "trace.html", "agent-trace.json", "context.html"]
    if run["mode"] == "gameplay":
        names.extend(("gameplay.json", "gameplay.html"))
    if run["mode"] == "workflow":
        names.append("workflow.json")
    receipt["files"] = []
    for name in names:
        path = store.run_dir(run_id) / "reports" / name
        try:
            receipt["files"].append({"ref": f"reports/{name}", "bytes": path.stat().st_size})
        except OSError:
            receipt["status"] = "degraded"
    try:
        atomic_json(store.run_dir(run_id) / "reports/publication.json", receipt)
    except Exception as exc:
        receipt["status"] = "degraded"
        receipt["receipt_error"] = report_error(exc)
    store.event(run_id, "report.finished", receipt)
    return receipt


def write_workflow_report(store, run_id):
    from game_automation.kernel.workflow.scheduler import workflow_status

    checkpoint = workflow_status(store, run_id)
    checkpoint["snapshot"] = snapshot_metadata(store, run_id)
    atomic_json(store.run_dir(run_id) / "reports/workflow.json", checkpoint)


def repair_pending_reports(store):
    """Retry once per entry, never once per action and never replay business work."""
    rows = store.db.execute(
        "SELECT DISTINCT run_id FROM events WHERE kind='report.requested' ORDER BY run_id"
    ).fetchall()
    repaired = []
    for row in rows:
        run_id = row[0]
        if store.run(run_id)["status"] not in SETTLED_STATUSES:
            continue
        if publication_status(store, run_id)["status"] != "complete":
            repaired.append(finalize_reports(store, run_id))
    return repaired
