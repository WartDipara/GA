"""Project recorded boundaries into semantic spans; never infer missing execution facts."""

import json

from .pricing import reference_cost

STARTS = {
    "call.started": "tool",
    "gameplay.step.started": "step",
    "workflow.stage.started": "chain",
}
ENDS = {
    "call.completed": "ok",
    "call.failed": "error",
    "call.incomplete": "incomplete",
    "gameplay.step.completed": "ok",
    "gameplay.step.failed": "error",
    "gameplay.step.incomplete": "incomplete",
    "workflow.stage.completed": "ok",
    "workflow.stage.failed": "error",
    "workflow.stage.incomplete": "incomplete",
}
DETAILS = (
    "workflow.stage.bound",
    "call.retry",
    "gameplay.decision",
    "gameplay.outcome",
    "gameplay.observation",
    "gameplay.planner.coordinates",
    "gameplay.action.origin",
    "gameplay.goal.completed",
    "action.intent",
    "action.dispatched",
    "action.result",
    "ai.runtime",
    "ai.session",
    "ai.item.started",
    "ai.item.completed",
    "ai.tool.started",
    "ai.tool.completed",
    "ai.usage",
    "ai.summary_availability",
    "run.state",
)
USAGE_ATTRIBUTES = {
    "inputTokens": "gen_ai.usage.input_tokens",
    "outputTokens": "gen_ai.usage.output_tokens",
    "cachedInputTokens": "gen_ai.usage.cache_read.input_tokens",
    "cacheWriteInputTokens": "gen_ai.usage.cache_creation.input_tokens",
    "reasoningOutputTokens": "gen_ai.usage.reasoning_output_tokens",
    "totalTokens": "gen_ai.usage.total_tokens",
}


def span_record(run_id, span_id, name, kind, start, parent=None):
    record = {
        "span_id": span_id,
        "parent_span_id": parent,
        "run_id": run_id,
        "name": name,
        "kind": kind,
        "start_time": start,
        "end_time": None,
        "duration_ms": None,
        "status": "unknown",
        "attributes": {},
        "links": [],
        "events": [],
    }
    return record


def enrich_span(span, event):
    payload = event["payload"]
    kind = event["kind"]
    attributes = span["attributes"]
    if event.get("request_id"):
        attributes["game_automation.request_id"] = event["request_id"]
    for field in ("input_ref", "output_ref", "attempt", "plugin", "action_id"):
        if field in payload:
            attributes[f"game_automation.{field}"] = payload[field]
    if kind == "ai.runtime":
        attributes["gen_ai.request.model"] = payload.get("model")
        attributes["game_automation.model_source"] = "reported runtime configuration"
    if payload.get("thread_id"):
        attributes["gen_ai.session.id"] = payload["thread_id"]
    if payload.get("turn_id"):
        attributes["gen_ai.turn.id"] = payload["turn_id"]
    if kind == "ai.session":
        attributes["gen_ai.agent.name"] = payload.get("role")
    if kind == "ai.summary_availability":
        attributes["game_automation.public_summary_available"] = payload.get("available")
    if kind == "gameplay.decision":
        attributes["game_automation.decision.reason_summary"] = payload.get("reason_summary")
        attributes["game_automation.decision.objective"] = payload.get("objective")
        attributes["game_automation.observation_id"] = payload.get("observation_id")
    if payload.get("child_run"):
        span["links"].append({"run_id": payload["child_run"], "relation": "stage_execution"})
    if payload.get("retry_of"):
        span["links"].append({"span_id": payload["retry_of"], "relation": "retry_of"})
    if kind in ENDS:
        span["status"] = ENDS[kind]
        span["end_time"] = event["timestamp"]
        duration = payload.get("duration_ms")
        span["duration_ms"] = duration if type(duration) in (int, float) and duration >= 0 else None
        error = payload.get("error") or payload.get("reason")
        if error:
            attributes["error.type"] = error.get("code", "unknown")
            attributes["exception.message"] = error.get("message")
    if kind not in ("ai.item.started", "ai.item.completed", "ai.usage"):
        reference = {
            "event_id": event["event_id"],
            "seq": event["seq"],
            "kind": kind,
        }
        span["events"].append(reference)


def project_trace(store, run_id):
    run = store.run(run_id)
    root = span_record(run_id, run_id, run["mode"], "entry", run["created_at"])
    if run["mode"] == "gameplay":
        root["kind"] = "agent"
        root["attributes"]["gen_ai.operation.name"] = "invoke_agent"
    root["status"] = {
        "running": "running",
        "queued": "unknown",
        "succeeded": "ok",
        "failed": "error",
        "blocked": "error",
        "cancelled": "incomplete",
    }[run["status"]]
    if run["status"] not in ("running", "queued"):
        root["end_time"] = run["updated_at"]
    inputs = store.run_task(run_id)
    if inputs.get("workflow_run"):
        root["links"].append({"run_id": inputs["workflow_run"], "relation": "owning_workflow"})
    for event in store.events(run_id, kinds=("run.continued_from",)):
        root["links"].append({"run_id": event["payload"]["run_id"], "relation": "continued_from"})
    kinds = (*STARTS, *ENDS, *DETAILS)
    events = store.events(run_id, kinds=kinds, limit=20_000)
    spans = {}
    session_totals = {}
    baselines = {}
    for event in events:
        if event["kind"] not in STARTS:
            continue
        payload = event["payload"]
        name = payload.get("capability") or payload.get("stage") or event["kind"]
        kind = "chat" if name == "ai.decide" else STARTS[event["kind"]]
        span = span_record(
            run_id, event["span_id"], name, kind, event["timestamp"], event["parent_span_id"]
        )
        span["attributes"]["gen_ai.operation.name"] = {
            "step": "react",
            "chat": "chat",
            "tool": "execute_tool",
            "chain": "chain",
        }[kind]
        if kind == "step":
            span["attributes"]["gen_ai.step.id"] = event["span_id"]
        spans[event["span_id"]] = span
    for span in spans.values():
        if span["parent_span_id"] not in spans:
            recorded_parent = span["parent_span_id"]
            if recorded_parent:
                span["attributes"]["game_automation.unavailable_parent"] = recorded_parent
            span["parent_span_id"] = run_id
    for event in events:
        target = spans.get(event["span_id"])
        if target is not None:
            enrich_span(target, event)
            payload = event["payload"]
            if event["kind"] == "ai.session":
                thread = payload.get("thread_id")
                baseline = session_totals.get(thread)
                if payload.get("fresh") is True:
                    baseline = dict.fromkeys(USAGE_ATTRIBUTES, 0)
                baselines[target["span_id"]] = baseline
            if event["kind"] == "ai.usage":
                usage = payload.get("usage") or {}
                total = usage.get("total") or {}
                baseline = baselines.get(target["span_id"])
                delta = None
                if baseline is not None:
                    delta = {
                        key: total[key] - baseline[key]
                        if type(total.get(key)) is int and type(baseline.get(key)) is int
                        else None
                        for key in USAGE_ATTRIBUTES
                    }
                    if any(value is not None and value < 0 for value in delta.values()):
                        delta = None
                target["usage"] = {
                    "attributed": delta,
                    "provider_last_report": usage.get("last"),
                    "session_total": total,
                }
                if target["attributes"].get("gen_ai.session.id"):
                    session_totals[target["attributes"]["gen_ai.session.id"]] = total
        else:
            root["events"].append(
                {"event_id": event["event_id"], "seq": event["seq"], "kind": event["kind"]}
            )
            if event["kind"] == "run.state" and event["payload"].get("reason"):
                root["attributes"]["game_automation.run_error"] = event["payload"]["reason"]
    for span in spans.values():
        if span["end_time"] is None:
            span["status"] = "running" if run["status"] == "running" else "incomplete"
        if span["kind"] == "chat":
            attributed = span.get("usage", {}).get("attributed")
            for key, name in USAGE_ATTRIBUTES.items():
                if attributed is not None and attributed.get(key) is not None:
                    span["attributes"][name] = attributed[key]
            span["cost"] = reference_cost(
                store.root, span["attributes"].get("gen_ai.request.model"), attributed
            )
            span["attributes"]["game_automation.usage_scope"] = "observed_session_counter_delta"
    for span in spans.values():
        if span["kind"] != "chat":
            continue
        agent = span["attributes"].get("gen_ai.agent.name")
        parent = spans.get(span["parent_span_id"])
        if agent and parent and parent["kind"] == "step":
            parent["attributes"]["gen_ai.agent.name"] = agent
    for span in spans.values():
        parent = spans.get(span["parent_span_id"])
        agent = parent["attributes"].get("gen_ai.agent.name") if parent else None
        if agent and "gen_ai.agent.name" not in span["attributes"]:
            span["attributes"]["gen_ai.agent.name"] = agent
    version = None
    manifest = store.run_dir(run_id) / "manifest.json"
    try:
        contents = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(contents, dict):
            raise ValueError("Invalid manifest")
        version = contents.get("framework_version")
    except (OSError, ValueError):
        root["attributes"]["game_automation.version_evidence"] = "unavailable"
    projection = {
        "schema_version": "1.0",
        "trace_id": run["task_id"],
        "run_id": run_id,
        "resource": {"service.name": "game-automation", "service.version": version},
        "scope": "recorded_application_boundaries",
        "event_limit_reached": len(events) == 20_000,
        "spans": [root, *spans.values()],
    }
    return projection
