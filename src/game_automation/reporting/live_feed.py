"""Bounded, cursor-based projection for the live monitor; no trace reconstruction."""

import json


def event_page(store, task_id, after, revision):
    ordering = "DESC" if after < 0 else "ASC"
    rows = store.db.execute(
        "SELECT e.seq,e.run_id,e.kind,e.span_id,"
        "json_extract(e.data,'$.parent_span_id') AS parent_span_id,"
        "json_extract(e.data,'$.timestamp') AS timestamp,"
        "json_extract(e.data,'$.payload.capability') AS capability,"
        "json_extract(e.data,'$.payload.duration_ms') AS duration_ms,"
        "substr(json_extract(e.data,'$.payload'),1,4001) AS detail "
        "FROM events e JOIN runs r ON r.id=e.run_id WHERE r.task_id=? AND e.seq>? "
        "AND (e.kind LIKE 'call.%' OR e.kind='run.state' "
        "OR e.kind LIKE 'workflow.stage.%' OR e.kind LIKE 'action.%' "
        "OR e.kind LIKE 'exploration.supervision.%' OR e.kind LIKE 'gameplay.watchdog.%' "
        "OR e.kind LIKE 'gameplay.lifecycle.%' OR e.kind='gameplay.finalization.issue' "
        "OR e.kind IN ('report.requested','report.finished')) "
        f"ORDER BY e.seq {ordering} LIMIT 201",
        (task_id, max(after, 0)),
    ).fetchall()
    has_more = after >= 0 and len(rows) > 200
    selected = rows[:200]
    if after < 0:
        selected.reverse()
    events = []
    for row in selected:
        detail = row["detail"] or "{}"
        event = {
            "seq": row["seq"],
            "timestamp": row["timestamp"],
            "run_id": row["run_id"],
            "kind": row["kind"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"],
            "label": row["capability"] or row["kind"],
            "duration_ms": row["duration_ms"],
            "detail": detail[:4000],
            "truncated": len(detail) > 4000,
        }
        if row["kind"] == "run.state":
            state = json.loads(detail) if len(detail) <= 4000 else {}
            event["label"] = f"{state.get('stage', 'workflow')} / {state.get('to', 'unknown')}"
        events.append(event)
    page = {
        "events": events,
        "revision": selected[-1]["seq"] if has_more else revision,
        "has_more": has_more,
    }
    return page
