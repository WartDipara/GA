"""Evidence-only gameplay report; generating it never contacts a model or device."""

import html
import json
from collections import Counter
from pathlib import Path

from game_automation.common import FrameworkError, atomic_text, within
from game_automation.errors import ErrorCode

from .action_evidence import action_figures
from .execution import snapshot_metadata

REPORT_KINDS = (
    "call.started",
    "gameplay.decision",
    "action.dispatched",
    "gameplay.action.origin",
    "gameplay.wait",
    "gameplay.planner.requested",
    "gameplay.correction",
    "gameplay.cache.hit",
    "gameplay.cache.invalidated",
    "gameplay.goal.completed",
    "gameplay.outcome",
    "ai.usage",
    "gameplay.battle.observed",
    "gameplay.battle.advanced",
    "gameplay.progress.restored",
    "gameplay.observation",
    "gameplay.recovery",
)


def action_path(store, run_id):
    directory = store.run_dir(run_id)
    terminal_kinds = ("call.completed", "call.failed", "call.incomplete")
    timings = {event["span_id"]: event for event in store.iter_events(run_id, kinds=terminal_kinds)}
    dispatched = {
        event["payload"]["action_id"]
        for event in store.iter_events(run_id, kinds=("action.dispatched",))
    }
    observations = {}
    path = []
    for action in store.action_records(run_id):
        issues = []
        try:
            request = json.loads(within(directory, action["input_ref"]).read_text(encoding="utf-8"))
            if not isinstance(request, dict):
                raise ValueError("Invalid retained request")
        except (FrameworkError, OSError, ValueError):
            request = {}
            issues.append("Retained action input is unavailable or invalid")
        timing = timings.get(action["span_id"])
        item = {
            "action_id": action["action_id"],
            "status": action["status"],
            "dispatched": action["action_id"] in dispatched,
            "proposal": request.get("action"),
            "span_id": action["span_id"],
            "input_ref": action["input_ref"],
            "result": action.get("result"),
            "execution_duration_ms": timing["payload"].get("duration_ms") if timing else None,
            "execution_status": timing["kind"].removeprefix("call.") if timing else "unknown",
            "evidence_issues": issues,
        }
        if request.get("observation_path"):
            try:
                item["before_image"] = (
                    store.artifact_path(run_id, request["observation_path"])
                    .relative_to(directory)
                    .as_posix()
                )
            except (FrameworkError, OSError, ValueError):
                issues.append("Before observation reference is unavailable")
        outcome = action.get("result") or {}
        after_run = outcome.get("observation_run") or run_id
        try:
            same_task = store.run(after_run)["task_id"] == store.run(run_id)["task_id"]
        except FrameworkError:
            same_task = False
            issues.append("After observation execution is unavailable")
        if same_task:
            if after_run not in observations:
                observations[after_run] = {
                    event["payload"]["observation_id"]: event["payload"]["evidence_ref"]
                    for event in store.iter_events(after_run, kinds=("gameplay.observation",))
                }
            after_image = observations[after_run].get(outcome.get("observation_id"))
            if after_image:
                item["after_image"] = after_image
                item["after_run"] = after_run
        path.append(item)
    return path


def write_html(store, run_id, report):
    def escaped(value):
        return html.escape(str(value), quote=True)

    rows = []
    timed_actions = sorted(
        (action for action in report["action_path"] if action["execution_duration_ms"] is not None),
        key=lambda action: action["execution_duration_ms"],
        reverse=True,
    )
    timing_rows = []
    for action in timed_actions[:20]:
        action_id = escaped(action["action_id"])
        action_type = escaped((action["proposal"] or {}).get("type", "unknown"))
        duration = action["execution_duration_ms"]
        timing_rows.append(
            f"<tr><td>{action_id}</td>"
            f"<td>{action_type}</td><td>{duration} ms</td>"
            f"<td>{escaped(action['execution_status'])}</td></tr>"
        )
    for action in report["action_path"][-2000:]:
        picture = action_figures(store, run_id, action)
        payload = json.dumps(action, ensure_ascii=False, indent=2)
        duration = action["execution_duration_ms"]
        execution_time = f"{duration} ms" if duration is not None else "Unknown"
        rows.append(
            f'<article id="{escaped(action["action_id"])}">'
            f"<h3>{escaped(action['action_id'])}</h3>"
            f"<p>Recorded action state: {escaped(action['status'])}</p>"
            f"<p>Execution time: {execution_time}; "
            f"call status: {escaped(action['execution_status'])}</p>"
            f"{picture}<pre>{escaped(payload)}</pre></article>"
        )
    decisions = "".join(
        f"<article><h3>AI proposal: {escaped(item['objective'])}</h3>"
        f"<p>{escaped(item['reason_summary'])}</p>"
        f"<p>Expected: {escaped(item['expected_outcome'])}</p></article>"
        for item in report["decisions"][-1000:]
    )
    summary = {
        key: report[key]
        for key in (
            "state",
            "snapshot",
            "goal",
            "ai_calls",
            "deterministic_steps",
            "dispatched_actions",
            "wait_duration_ms",
            "verified_business_goals",
            "stage_advances",
        )
    }
    evidence_style = (Path(__file__).parent / "web/action-evidence.css").read_text(encoding="utf-8")
    document = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src 'self' file: data:; style-src 'unsafe-inline'\">"
        "<title>Gameplay evidence</title><style>body{background:#101820;color:#e6edf3;"
        "font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px}"
        "article{background:#1c2833;padding:20px;margin:16px 0;border-radius:10px}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:280px}a{color:#84caff}"
        "table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;"
        "border-bottom:1px solid #364655;overflow-wrap:anywhere}"
        f"{evidence_style}</style>"
        f"<h1>Gameplay evidence</h1><p>{escaped(run_id)}</p>"
        "<p>AI proposals, dispatched actions and verified outcomes are separate records. "
        "A local predicate verifies only its stated condition.</p>"
        f"<pre>{escaped(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>"
        '<p><a href="gameplay.json">Complete gameplay data</a> · '
        '<a href="trace.html">Calls and model context</a> · '
        '<a href="exploration.html">Exploration catalog</a></p>'
        "<p>HTML shows the latest 2,000 actions and 1,000 decisions. "
        "The JSON report retains the complete batch history.</p>"
        "<h2>Longest execution times</h2>"
        "<p>Up to 20 actions, ordered by measured execution time. Timing covers the device "
        "execution call, including safety checks and plugin overhead. AI planning, subsequent "
        "waiting, log collection and outcome review are excluded. Missing timing is unknown; "
        "failed or incomplete calls show elapsed time, not proof of device completion.</p>"
        "<table><thead><tr><th>Action</th><th>Type</th><th>Execution time</th>"
        "<th>Call status</th></tr></thead><tbody>"
        + "".join(timing_rows)
        + "</tbody></table><h2>Action path</h2>"
        + "".join(rows)
        + "<h2>Decision explanations</h2>"
        + decisions
    )
    target = store.run_dir(run_id) / "reports/gameplay.html"
    atomic_text(target, document)


def gameplay_report(store, run_id):
    if store.run(run_id)["mode"] != "gameplay":
        raise FrameworkError(ErrorCode.INVALID_STATE, "This run has no gameplay report")
    events = []
    for event in store.iter_events(run_id, kinds=REPORT_KINDS):
        if event["kind"] == "gameplay.observation":
            event["payload"] = {
                key: event["payload"][key] for key in ("observation_id", "evidence_ref")
            }
        events.append(event)
    task = store.run_task(run_id)

    def selected(kind):
        values = [event["payload"] for event in events if event["kind"] == kind]
        return values

    starts = selected("call.started")
    decisions = selected("gameplay.decision")
    actions = selected("action.dispatched")
    origins = selected("gameplay.action.origin")
    waits = selected("gameplay.wait")
    reasons = Counter(item["reason"] for item in selected("gameplay.planner.requested"))
    report = {
        "run_id": run_id,
        "state": store.run(run_id)["status"],
        "snapshot": snapshot_metadata(store, run_id),
        "goal": task["goal"],
        "ai_calls": sum(item["capability"] == "ai.decide" for item in starts),
        "ai_trigger_reasons": dict(reasons),
        "deterministic_steps": sum(item["source"] != "planner" for item in origins),
        "dispatched_actions": len(actions),
        "action_path": action_path(store, run_id),
        "decisions": decisions,
        "wait_duration_ms": sum(item["duration_ms"] for item in waits),
        "corrections": selected("gameplay.correction"),
        "cache_hits": selected("gameplay.cache.hit"),
        "cache_invalidations": selected("gameplay.cache.invalidated"),
        "verified_business_goals": selected("gameplay.goal.completed"),
        "outcomes": selected("gameplay.outcome"),
        "battle_progress": selected("gameplay.battle.observed"),
        "stage_advances": selected("gameplay.battle.advanced"),
        "usage": selected("ai.usage") or None,
        "checkpoint": next(
            (
                event["payload"]
                for event in store.events(run_id, kinds=("gameplay.checkpoint",), limit=1)
            ),
            None,
        ),
        "restored_from": selected("gameplay.progress.restored"),
        "observations": [
            {"observation_id": item["observation_id"], "evidence_ref": item["evidence_ref"]}
            for item in selected("gameplay.observation")
        ],
        "coverage": "Only recorded reachable interactions; no authoritative whole-game inventory",
    }
    store.artifact(run_id, "reports/gameplay.json", report)
    write_html(store, run_id, report)
    return report
