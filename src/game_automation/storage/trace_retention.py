"""Explicit, task-scoped retention using the existing coordinated cleanup journal."""

import heapq
import json
import tomllib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from game_automation.common import FileLock, FrameworkError
from game_automation.contracts import validate
from game_automation.errors import ErrorCode

from .cleanup import cleanup


def settings(store):
    source = tomllib.loads((store.root / "config/application.toml").read_text(encoding="utf-8"))
    policy = source.get("observability", {})
    validate(store.root / "contracts/v1", "observability-policy", policy)
    return policy


def pin_task(store, run_id, pinned):
    with FileLock(store.var / "execution.lock"):
        payload = {"pinned": pinned, "policy_version": "1"}
        event = store.event(run_id, "task.retention.updated", payload)
        result = {"task_id": event["task_id"], "pinned": pinned, "event_id": event["event_id"]}
        return result


def retention_plan(store, days=None):
    days = settings(store).get("retention_days", 0) if days is None else days
    if type(days) is not int or not 0 <= days <= 36500:
        raise FrameworkError(
            ErrorCode.OBSERVABILITY_INVALID, "Retention days must be 0 through 36500"
        )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    tasks = defaultdict(list)
    owners = {}
    protected = {}
    dependencies = []
    for row in store.db.execute(
        "SELECT r.id,r.task_id,r.status,r.updated_at,i.spec FROM runs r "
        "JOIN run_inputs i ON i.run_id=r.id "
        "WHERE r.task_id NOT IN (SELECT alias_id FROM task_aliases)"
    ):
        tasks[row["task_id"]].append(row)
        owners[row["id"]] = row["task_id"]
        spec = json.loads(row["spec"])
        dependencies.extend(
            (row["task_id"], spec[key]) for key in ("source_run", "prepared_run") if spec.get(key)
        )
    latest = {}
    for row in store.db.execute(
        "SELECT e.run_id,e.kind,e.data FROM events e JOIN runs r ON r.id=e.run_id "
        "WHERE e.kind IN ('report.requested','report.finished','task.retention.updated') "
        "ORDER BY e.seq"
    ):
        value = json.loads(row["data"])["payload"]
        task_id = owners.get(row["run_id"])
        if row["kind"] == "task.retention.updated" and task_id:
            if value.get("pinned") is False:
                protected.pop(task_id, None)
            else:
                protected[task_id] = "pinned"
        else:
            latest[(row["run_id"], row["kind"])] = value
    for row in store.db.execute(
        "SELECT DISTINCT r.task_id FROM actions a JOIN runs r ON r.id=a.run_id "
        "WHERE a.status IN ('prepared','unknown','executed')"
    ):
        protected.setdefault(row[0], "unresolved_action")
    candidates = {}
    for task_id, runs in tasks.items():
        if task_id in protected:
            continue
        if any(run["status"] not in ("succeeded", "failed", "cancelled") for run in runs):
            protected[task_id] = "active_or_resumable"
            continue
        try:
            updated = max(datetime.fromisoformat(run["updated_at"]) for run in runs)
            if updated.tzinfo is None:
                raise ValueError("Timestamp has no timezone")
        except (TypeError, ValueError):
            protected[task_id] = "unknown_age"
            continue
        if cutoff is None or updated >= cutoff:
            protected[task_id] = "within_retention"
            continue
        for run in runs:
            request = latest.get((run["id"], "report.requested"))
            receipt = latest.get((run["id"], "report.finished"))
            if (
                not request
                or not receipt
                or receipt.get("status") != "complete"
                or receipt.get("request_id") != request.get("request_id")
            ):
                protected[task_id] = "report_pending"
                break
        if task_id not in protected:
            candidates[task_id] = updated
    changed = True
    while changed:
        changed = False
        for source, referenced in dependencies:
            target = owners.get(referenced)
            if source not in candidates and target in candidates:
                candidates.pop(target)
                protected[target] = "referenced_by_retained_task"
                changed = True
    referenced = defaultdict(set)
    dependents = defaultdict(set)
    for source, run in dependencies:
        target = owners.get(run)
        if source in candidates and target in candidates and source != target:
            referenced[source].add(target)
            dependents[target].add(source)
    available = [(age, task) for task, age in candidates.items() if not dependents[task]]
    heapq.heapify(available)
    selected = []
    while available and len(selected) < 200:
        _, task = heapq.heappop(available)
        selected.append(task)
        for target in referenced[task]:
            dependents[target].discard(task)
            if not dependents[target]:
                heapq.heappush(available, (candidates[target], target))
    eligible_count = len(candidates)
    if not available and len(selected) < len(candidates):
        for task in candidates.keys() - set(selected):
            protected[task] = "dependency_group_requires_explicit_cleanup"
        eligible_count = len(selected)
    result = {
        "days": days,
        "enabled": bool(days),
        "cutoff": cutoff.isoformat() if cutoff else None,
        "task_ids": selected,
        "eligible_tasks": eligible_count,
        "protected_counts": dict(Counter(protected.values())),
        "has_more": eligible_count > len(selected),
    }
    validate(store.root / "contracts/v1", "trace-retention-plan", result)
    return result


def apply_retention(store, days=None):
    selected_plan = None

    def select():
        nonlocal selected_plan
        selected_plan = retention_plan(store, days)
        return selected_plan["task_ids"]

    removed = cleanup(store, apply=True, selection=select)
    result = {"plan": selected_plan, "cleanup": removed}
    return result
