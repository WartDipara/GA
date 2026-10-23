"""Append human judgments to task-owned events and derive a local reference dataset."""

import json
import os
import re
import tempfile

from game_automation.common import FileLock, FrameworkError
from game_automation.errors import ErrorCode
from game_automation.reporting.agent_metrics import version_identity
from game_automation.reporting.agent_trace import agent_view


def actor_reference(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise FrameworkError(
            ErrorCode.OBSERVABILITY_INVALID, "Use an opaque alphanumeric actor reference"
        )
    return value


def record_review(store, run_id, span_id, metric, label, evidence_refs, note, actor_ref):
    if metric not in ("accuracy", "tool_correctness", "task_completion") or label not in (
        "pass",
        "fail",
        "unknown",
    ):
        raise FrameworkError(ErrorCode.OBSERVABILITY_INVALID)
    run = store.run(run_id)
    spans = store.events(
        run_id,
        span=span_id,
        kinds=("call.started", "gameplay.step.started", "workflow.stage.started"),
        limit=1,
    )
    if not 1 <= len(evidence_refs) <= 100:
        raise FrameworkError(ErrorCode.OBSERVABILITY_INVALID)
    placeholders = ",".join("?" for _ in evidence_refs)
    evidence = {
        row[0]
        for row in store.db.execute(
            f"SELECT event_id FROM events WHERE run_id=? AND event_id IN ({placeholders})",
            (run_id, *evidence_refs),
        )
    }
    if (not spans and span_id != run_id) or not set(evidence_refs).issubset(evidence):
        raise FrameworkError(
            ErrorCode.OBSERVABILITY_INVALID, "Review needs a recorded span and event evidence"
        )
    if not note.strip() or len(note) > 2000:
        raise FrameworkError(
            ErrorCode.OBSERVABILITY_INVALID, "Review note requires 1 to 2,000 characters"
        )
    payload = {
        "span_id": span_id,
        "metric": metric,
        "label": label,
        "evidence_refs": evidence_refs,
        "note": note,
        "actor_ref": actor_reference(actor_ref),
        "version": version_identity(store, run_id),
    }
    with FileLock(store.var / "layout.lock"):
        store.event(run_id, "agent.review", payload, span_id=span_id)
        return _export_dataset(store, run["task_id"])


def export_dataset(store, task_id):
    with FileLock(store.var / "layout.lock"):
        return _export_dataset(store, task_id)


def _export_dataset(store, task_id):
    store.task(task_id)
    latest = {}
    for run in store.task_runs(task_id):
        for event in store.iter_events(run["run_id"], kinds=("agent.review",)):
            review = event["payload"]
            key = (run["run_id"], review["span_id"], review["metric"], review["actor_ref"])
            latest[key] = {
                "task_id": task_id,
                "run_id": run["run_id"],
                "review_event_id": event["event_id"],
                **review,
            }
    records = [review for review in latest.values() if review["label"] != "unknown"]
    directory = store.task_dir(task_id) / "datasets"
    directory.mkdir(exist_ok=True)
    target = directory / "agent-reviews.jsonl"
    handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    result = {
        "dataset": str(target),
        "labeled_cases": len(records),
        "basis": "Human labels with retained source evidence; not an executed benchmark",
    }
    return result


def compare_traces(store, first, second):
    views = [agent_view(store, identifier) for identifier in (first, second)]
    fingerprints = [view["version"]["skill_fingerprint"] for view in views]
    comparison = {
        "runs": [
            {
                "run_id": view["run_id"],
                "version": view["version"],
                "metrics": view["metrics"],
                "quality": view["quality"],
            }
            for view in views
        ],
        "same_skill_version": fingerprints[0] == fingerprints[1] if all(fingerprints) else None,
        "interpretation": (
            "Observed samples and label coverage; differences do not establish causality "
            "or equal task difficulty"
        ),
    }
    return comparison
