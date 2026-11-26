"""Bounded, cursor-based projection for the live monitor; no trace reconstruction."""

import json

from game_automation.storage.sql_queries import read_query


def event_page(store, task_id, after, revision):
    statement = read_query("live-events-latest" if after < 0 else "live-events-forward")
    parameters = {"task_id": task_id, "after": max(after, 0)}
    rows = store.db.execute(statement, parameters).fetchall()
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
