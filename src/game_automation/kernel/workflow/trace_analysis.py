"""User-requested natural-language analysis, isolated from device execution."""

from game_automation.common import FrameworkError
from game_automation.contracts import schema, validate
from game_automation.errors import ErrorCode
from game_automation.prompting.snapshots import build_input
from game_automation.reporting.agent_trace import agent_view


async def analyze_trace(store, run_id, host, manifest, task):
    source = task["source_run"]
    if store.run(source)["task_id"] != store.run(run_id)["task_id"]:
        raise FrameworkError(ErrorCode.OBSERVABILITY_INVALID)
    view = agent_view(store, source)
    candidates = [
        span
        for span in view["spans"]
        if span["kind"] in ("chat", "step") or span["status"] == "error"
    ]
    slowest = sorted(
        (span for span in view["spans"] if span["duration_ms"] is not None),
        key=lambda span: span["duration_ms"],
        reverse=True,
    )[:20]
    selected = {span["span_id"]: span for span in [*candidates, *slowest]}
    selected_spans = list(selected.values())[:200]
    context = {
        "question": task["question"],
        "source_run": source,
        "version": view["version"],
        "metrics": view["metrics"],
        "quality": view["quality"],
        "spans": selected_spans,
        "selection": "Model turns, steps, errors and 20 slowest measured spans; at most 200 nodes",
        "omitted_spans": len(view["spans"]) - len(selected_spans),
    }
    store.artifact(run_id, "artifacts/analysis-context.json", context)
    workspace = store.run_dir(run_id) / "ai-workspace"
    workspace.mkdir(exist_ok=True)
    turn = build_input(
        store, run_id, "observability", context, schema(host.contracts, "trace-analysis")
    )
    params = {
        "codex": manifest["application"]["codex"],
        "workspace": str(workspace),
        "role": "trace-analyst",
        "thread_id": None,
        "turn": turn,
    }
    response = await host.call("ai.decide", params)
    analysis = response["decision"]
    validate(host.contracts, "trace-analysis", analysis)
    known = {span["span_id"] for span in selected_spans}
    known.update(event["event_id"] for span in selected_spans for event in span["events"])
    if any(not set(finding["evidence_refs"]).issubset(known) for finding in analysis["findings"]):
        raise FrameworkError(
            ErrorCode.AI_OUTPUT_INVALID, "Analysis cited evidence outside its recorded scope"
        )
    result = {
        "source_run": source,
        "question": task["question"],
        "baseline_version": view["version"],
        **analysis,
    }
    store.artifact(run_id, "reports/analysis.json", result)
    host.record("agent.analysis.completed", result)
