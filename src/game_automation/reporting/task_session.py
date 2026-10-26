"""Paged business-session navigation across execution batches and recovery segments."""

import json
from collections import defaultdict
from urllib.parse import quote, urlencode

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.errors import ErrorCode


def task_session(store, run_id, *, before=0, limit=50):
    if not 1 <= limit <= 200 or before < 0:
        raise FrameworkError(ErrorCode.OBSERVABILITY_INVALID, "Invalid session page")
    task_id = store.run(run_id)["task_id"]
    rows = store.db.execute(
        "SELECT r.rowid AS cursor,r.id,r.mode,r.status,i.spec FROM runs r "
        "JOIN run_inputs i ON i.run_id=r.id WHERE r.task_id=? AND (?=0 OR r.rowid<?) "
        "ORDER BY r.rowid DESC LIMIT ?",
        (task_id, before, before, limit + 1),
    ).fetchall()
    selected = rows[:limit]
    records = defaultdict(dict)
    links = defaultdict(list)
    totals = {}
    if selected:
        placeholders = ",".join("?" for _ in selected)
        identifiers = [row["id"] for row in selected]
        starts = store.db.execute(
            "WITH ranked AS (SELECT run_id,span_id,data,"
            "COUNT(*) OVER(PARTITION BY run_id) AS total,"
            "ROW_NUMBER() OVER(PARTITION BY run_id ORDER BY seq DESC) AS position "
            f"FROM events WHERE run_id IN ({placeholders}) AND kind='execution.segment.started') "
            "SELECT * FROM ranked WHERE position<=20",
            identifiers,
        ).fetchall()
        for row in starts:
            payload = json.loads(row["data"])["payload"]
            totals[row["run_id"]] = row["total"]
            records[row["run_id"]][row["span_id"]] = {
                "span_id": row["span_id"],
                "attempt_id": payload["attempt_id"],
                "previous_span_id": payload.get("previous_span_id"),
                "status": "unknown",
                "duration_ms": None,
            }
        for row in store.db.execute(
            f"SELECT run_id,span_id,data,kind FROM events WHERE run_id IN ({placeholders}) "
            "AND kind IN ('execution.segment.finished','execution.segment.incomplete',"
            "'run.continued_from') "
            "ORDER BY seq",
            identifiers,
        ):
            payload = json.loads(row["data"])["payload"]
            if row["kind"] == "run.continued_from":
                links[row["run_id"]].append(
                    {"relation": "continued_from", "run_id": payload["run_id"]}
                )
            elif row["span_id"] in records[row["run_id"]]:
                records[row["run_id"]][row["span_id"]].update(
                    status=payload.get("status", "unknown"), duration_ms=payload.get("duration_ms")
                )
    executions = []
    for row in selected:
        spec = json.loads(row["spec"])
        relations = links[row["id"]]
        if spec.get("workflow_run"):
            relations.append({"relation": "owning_workflow", "run_id": spec["workflow_run"]})
        href = f"../../{quote(row['id'], safe='')}/reports/trace.html"
        segments = list(records[row["id"]].values())
        for segment in segments:
            parameters = urlencode({"view": "calls", "span": segment["span_id"]})
            segment["href"] = f"{href}#{parameters}"
        durations = [item["duration_ms"] for item in segments if item["duration_ms"] is not None]
        execution = {
            "run_id": row["id"],
            "mode": row["mode"],
            "status": row["status"],
            "href": f"{href}#view=sessions",
            "relations": relations,
            "segments": segments,
            "total_segments": totals.get(row["id"], 0),
            "segments_truncated": totals.get(row["id"], 0) > len(segments),
            "visible_active_ms": sum(durations) if durations else None,
        }
        executions.append(execution)
    result = {
        "session_id": task_id,
        "scope": "business_task",
        "executions": executions,
        "next_before": selected[-1]["cursor"] if len(rows) > limit and selected else None,
        "before": before,
        "limit": limit,
        "source_event_seq": store.db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id IN "
            "(SELECT id FROM runs WHERE task_id=?)",
            (task_id,),
        ).fetchone()[0],
    }
    validate(store.root / "contracts/v1", "task-session", result)
    return result
