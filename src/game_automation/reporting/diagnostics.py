"""Deterministic, read-only integrity checks over retained execution evidence."""

import json
from collections import Counter
from urllib.parse import urlencode

from game_automation.common import FrameworkError, within
from game_automation.contracts import validate

from .trace_projection import ENDS, STARTS


def writer_health(store, run_id, through=None):
    events = list(
        store.iter_events(run_id, kinds=("trace.writer.health",), limit=1, through=through)
    )
    health = events[0]["payload"] if events else None
    if health is not None:
        validate(store.root / "contracts/v1", "writer-health", health)
    return health


def trace_diagnostics(store, run_id):
    owns_snapshot = not store.db.in_transaction
    if owns_snapshot:
        store.db.execute("BEGIN")
    try:
        return inspect_snapshot(store, run_id)
    finally:
        if owns_snapshot:
            store.db.rollback()


def inspect_snapshot(store, run_id):
    run = store.run(run_id)
    watermark = store.db.execute(
        "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    starts = {}
    endings = {}
    references = {}
    findings = []
    counts = Counter()
    attention = False

    def finding(code, severity, detail, event=None, span=None):
        nonlocal attention
        attention |= severity in ("warning", "error")
        counts[code] += 1
        if len(findings) >= 1000:
            return
        parameters = urlencode({"view": "calls", "span": span or run_id})
        item = {
            "code": code,
            "severity": severity,
            "span_id": span,
            "event_id": event["event_id"] if event else None,
            "detail": detail,
            "report_href": f"reports/trace.html#{parameters}",
        }
        findings.append(item)

    for event in store.iter_events(run_id, kinds=(*STARTS, *ENDS), through=watermark):
        span = event["span_id"]
        if event["kind"] in STARTS:
            if span in starts:
                finding("duplicate_start", "error", "Span has multiple starts.", event, span)
            starts[span] = event
        else:
            if span in endings:
                finding("duplicate_end", "error", "Span has multiple endings.", event, span)
            endings[span] = event
        for key in ("input_ref", "output_ref"):
            reference = event["payload"].get(key)
            if isinstance(reference, str):
                references.setdefault(reference, event)

    for span, event in starts.items():
        if span not in endings:
            active = run["status"] == "running"
            finding(
                "open_call" if active else "missing_end",
                "info" if active else "warning",
                "No recorded ending; execution outcome remains unknown.",
                event,
                span,
            )
        parent = event.get("parent_span_id")
        if parent and parent != run_id and parent not in starts:
            finding(
                "unavailable_parent",
                "warning",
                "Parent span is unavailable in this run.",
                event,
                span,
            )
    for span, event in endings.items():
        if span not in starts:
            finding("missing_start", "error", "Ending has no recorded start.", event, span)
        if event["kind"].endswith("incomplete"):
            finding("incomplete_call", "warning", "Call explicitly ended incomplete.", event, span)

    directory = store.run_dir(run_id)
    for reference, event in references.items():
        try:
            available = within(directory, reference).is_file()
        except (FrameworkError, OSError, ValueError):
            available = False
        if not available:
            finding("missing_artifact", "error", reference, event, event["span_id"])

    for row in store.db.execute(
        "SELECT action_id,status,result FROM actions WHERE run_id=? "
        "AND status IN ('prepared','unknown','executed')",
        (run_id,),
    ):
        action = json.loads(row["result"])
        finding(
            "unresolved_action",
            "warning",
            f"{row['action_id']}: {row['status']}; observe and reconcile before continuing.",
            span=action.get("span_id"),
        )
    requested = list(
        store.iter_events(run_id, kinds=("report.requested",), through=watermark, limit=1)
    )
    finished = list(
        store.iter_events(run_id, kinds=("report.finished",), through=watermark, limit=1)
    )
    if requested and (
        not finished
        or finished[0]["payload"].get("request_id") != requested[0]["payload"].get("request_id")
        or finished[0]["payload"].get("status") != "complete"
    ):
        finding("report_pending", "warning", "Report-only recovery is required.", requested[0])
    result = {
        "run_id": run_id,
        "source_event_seq": watermark,
        "status": "attention" if attention else "healthy",
        "counts": dict(counts),
        "findings": findings,
        "truncated": sum(counts.values()) > len(findings),
        "health": writer_health(store, run_id, watermark),
    }
    validate(store.root / "contracts/v1", "trace-diagnostics", result)
    return result
