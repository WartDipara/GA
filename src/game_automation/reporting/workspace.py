"""Small, read-only chart projections over authoritative execution records."""

import json
import math
from collections import Counter
from datetime import datetime
from statistics import median

from game_automation.contracts import validate
from game_automation.storage.sql_queries import read_query

STATE_TONES = {
    "queued": "info",
    "running": "accent",
    "succeeded": "positive",
    "blocked": "warning",
    "failed": "negative",
    "cancelled": "muted",
}


def workspace_view(store, *, run_id=None, task_id=None, links=()):
    kind = "execution" if run_id else "task" if task_id else "history"
    result = {
        "kind": kind,
        "title": "Execution insights" if run_id else "Workspace overview",
        "subtitle": "Recorded execution data",
        "status": "local",
        "links": list(links),
        "summary": [],
        "states": [],
        "activity": [],
        "latency": [],
        "histogram": [],
        "p50_ms": None,
        "p95_ms": None,
        "missing_timings": 0,
    }
    if store is None:
        return result
    if run_id:
        run = store.run(run_id)
        task_id = run["task_id"]
        result["status"] = run["status"]
        result["subtitle"] = "This execution · measured module calls"
        if run["status"] in ("queued", "running"):
            return result
        rows = store.db.execute(read_query("execution-call-latency"), {"run_id": run_id})
        durations = []
        states = Counter()
        for row in rows:
            event = json.loads(row["data"])
            started = event["payload"]
            end = json.loads(row["ending"]) if row["ending"] else None
            unfinished = "running" if run["status"] == "running" else "incomplete"
            states[end["kind"].split(".")[-1] if end else unfinished] += 1
            timing = end["payload"].get("duration_ms") if end else None
            if isinstance(timing, (int, float)) and math.isfinite(timing) and timing >= 0:
                durations.append(timing)
                result["latency"].append(
                    {
                        "label": started.get("capability", "Unknown operation"),
                        "value": timing,
                        "recorded_at": event["timestamp"],
                        "sequence": row["seq"],
                        "href": f"trace.html#view=calls&span={row['span_id']}",
                        "tone": "accent" if started.get("capability") == "ai.decide" else "info",
                    }
                )
            else:
                result["missing_timings"] += 1
        result["latency"] = sorted(result["latency"], key=lambda item: item["value"], reverse=True)[
            :6
        ]
        result["states"] = [
            {
                "label": state,
                "count": count,
                "tone": {
                    "completed": "positive",
                    "failed": "negative",
                    "incomplete": "warning",
                }.get(state, "accent"),
            }
            for state, count in states.items()
        ]
        if durations:
            durations.sort()
            result["p50_ms"] = median(durations)
            result["p95_ms"] = durations[math.ceil(len(durations) * 0.95) - 1]
            width = max(1000, math.ceil(durations[-1] / 6 / 1000) * 1000)
            for index in range(6):
                count = sum(min(5, int(value // width)) == index for value in durations)
                result["histogram"].append(
                    {
                        "label": f"{index * width / 1000:g}–{(index + 1) * width / 1000:g}s",
                        "count": count,
                    }
                )
    else:
        if task_id:
            rows = store.db.execute(
                "SELECT status,created_at FROM runs WHERE task_id=?", (task_id,)
            ).fetchall()
            retained_count = len(rows)
        else:
            rows = store.db.execute("SELECT status,created_at FROM runs").fetchall()
            retained_count = store.db.execute(read_query("canonical-task-count")).fetchone()[0]
        states = Counter(row["status"] for row in rows)
        days = Counter(
            datetime.fromisoformat(row["created_at"]).astimezone().date().isoformat()
            for row in rows
        )
        result["activity"] = [
            {"label": day, "count": count} for day, count in sorted(days.items())[-14:]
        ]
        result["states"] = [
            {"label": state, "count": count, "tone": STATE_TONES[state]}
            for state, count in states.items()
        ]
        summary = [
            (
                "Executions" if task_id else "Tasks",
                retained_count,
                "Retained history",
                "accent",
            ),
            ("Completed", states["succeeded"], "Execution outcomes", "positive"),
            ("Running", states["running"], "Currently active", "info"),
            (
                "Blocked / failed",
                states["blocked"] + states["failed"],
                "Recorded outcomes",
                "warning",
            ),
        ]
        result["summary"] = [
            {"label": label, "value": count, "note": note, "tone": tone}
            for label, count, note, tone in summary
        ]
        result["subtitle"] = "All recorded executions" if not task_id else "This task's executions"
    validate(store.root / "contracts/v1", "workspace-view", result)
    return result
