"""Evidence-only gameplay report; generating it never contacts a model or device."""

import html
import json
from collections import Counter
from pathlib import Path

from game_automation.common import FrameworkError, within
from game_automation.errors import ErrorCode

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
    path = []
    for action in store.action_records(run_id):
        request = json.loads(within(directory, action["input_ref"]).read_text(encoding="utf-8"))
        item = {
            "action_id": action["action_id"],
            "status": action["status"],
            "proposal": request.get("action"),
            "span_id": action["span_id"],
            "input_ref": action["input_ref"],
            "result": action.get("result"),
        }
        if request.get("observation_path"):
            item["before_image"] = (
                Path(request["observation_path"]).relative_to(directory).as_posix()
            )
        path.append(item)
    return path


def write_html(store, run_id, report):
    def escaped(value):
        return html.escape(str(value), quote=True)

    rows = []
    images = {item["observation_id"]: item["evidence_ref"] for item in report["observations"]}
    for action in report["action_path"][-2000:]:
        image = action.get("before_image")
        picture = (
            f'<img loading="lazy" src="../{escaped(image)}" alt="Before action">' if image else ""
        )
        after = images.get((action.get("result") or {}).get("observation_id"))
        if after:
            picture += f'<img loading="lazy" src="../{escaped(after)}" alt="After action">'
        payload = json.dumps(action, ensure_ascii=False, indent=2)
        rows.append(
            f"<article><h3>{escaped(action['action_id'])}</h3>"
            f"<p>Recorded action state: {escaped(action['status'])}</p>"
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
            "goal",
            "ai_calls",
            "deterministic_steps",
            "dispatched_actions",
            "wait_duration_ms",
            "verified_business_goals",
            "stage_advances",
        )
    }
    document = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src 'self' file: data:; style-src 'unsafe-inline'\">"
        "<title>Gameplay evidence</title><style>body{background:#101820;color:#e6edf3;"
        "font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px}"
        "article{background:#1c2833;padding:20px;margin:16px 0;border-radius:10px}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:280px}a{color:#84caff}</style>"
        f"<h1>Gameplay evidence</h1><p>{escaped(run_id)}</p>"
        "<p>AI proposals, dispatched actions and verified outcomes are separate records. "
        "A local predicate verifies only its stated condition.</p>"
        f"<pre>{escaped(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>"
        '<p><a href="gameplay.json">Complete gameplay data</a> · '
        '<a href="trace.html">Calls and model context</a></p>'
        "<p>HTML shows the latest 2,000 actions and 1,000 decisions. "
        "The JSON report retains the complete batch history.</p><h2>Action path</h2>"
        + "".join(rows)
        + "<h2>Decision explanations</h2>"
        + decisions
    )
    target = store.run_dir(run_id) / "reports/gameplay.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8", newline="\n")


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
    task = store.task(store.run(run_id)["task_id"])

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
