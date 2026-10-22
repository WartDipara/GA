"""Read-only event projection, inspired by dsh-context's fold/view separation.

Only records visible at the application boundary are reconstructed. A Codex turn
is not an underlying model request, and observed text is not the full context.
"""

from __future__ import annotations

import json
import math
from collections import Counter

from game_automation.common import FrameworkError, digest, redact, skill_prefix, within

CATEGORIES = {
    "instructions": "Application instructions",
    "skills": "Skill injections",
    "inputs": "Task input",
    "schema": "Output schema",
    "assistant": "Assistant output",
    "reasoning": "Public reasoning summary",
    "tools": "Tool calls and results",
    "attachments": "Attachment references",
}
USAGE_KEYS = (
    "inputTokens",
    "outputTokens",
    "cachedInputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)


def text_of(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def counters(value):
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        if isinstance(value.get(key), int) and not isinstance(value[key], bool) and value[key] >= 0
        else None
        for key in USAGE_KEYS
    }
    return result if any(value is not None for value in result.values()) else None


def project(events, run, read_text):
    """Deterministically rebuild a view without importing a provider or executing tools.

    read_text(relative_path) returns a bounded, redacted snapshot preview. File
    errors are visible as notes. The projection only reads committed records.
    """
    requests = []
    by_span = {}
    by_turn = {}
    pending = {}
    call_spans = set()
    context_events = []
    instructions = {}
    fresh_threads = set()
    totals = {}
    category_names = dict(CATEGORIES)
    raw_spans = {event.get("span_id") for event in events if event["kind"] == "provider.wire"}

    def make_request(event, params, source):
        span = event.get("span_id")
        request = {
            "id": f"request_{event['seq']}",
            "seq": event["seq"],
            "span_id": span,
            "request_id": event.get("request_id"),
            "thread_id": params.get("threadId"),
            "turn_id": None,
            "timestamp": event["timestamp"],
            "source": source,
            "boundary": "app_server_turn" if source == "provider.wire" else "framework_intent",
            "status": "incomplete",
            "items": [],
            "notes": [],
            "usage": None,
            "_baseline": None,
            "_items": {},
            "_output": {},
            "_summary": {},
        }
        requests.append(request)
        by_span[span] = request
        if source == "provider.wire":
            thread = request["thread_id"]
            request["_baseline"] = totals.get(
                thread, {key: 0 for key in USAGE_KEYS} if thread in fresh_threads else None
            )
        for name in ("baseInstructions", "developerInstructions"):
            if instructions.get(span, {}).get(name):
                item(request, "instructions", name, instructions[span][name], event, name)
        frozen = []
        skill_snapshots = {}
        for entry in params.get("input", []):
            if isinstance(entry, dict) and entry.get("type") == "skill":
                skill_name = entry.get("name", "")
                relative = f"skills/{skill_name}/SKILL.md"
                if skill_name not in skill_snapshots:
                    skill_snapshots[skill_name] = read_text(relative)
                content, _ = skill_snapshots[skill_name]
                if content is not None:
                    frozen.append((entry.get("name", ""), content))
        materialized = skill_prefix(frozen) if frozen else None
        for index, entry in enumerate(params.get("input", [])):
            if not isinstance(entry, dict):
                continue
            key = f"input_{index}"
            if entry.get("type") == "skill":
                relative = f"skills/{entry.get('name', '')}/SKILL.md"
                content, note = skill_snapshots[entry.get("name", "")]
                item(
                    request,
                    "skills",
                    entry.get("name", "Skill"),
                    content or "",
                    event,
                    key,
                    artifact_ref=relative,
                    availability="available" if content is not None else "unavailable",
                )
                if note:
                    request["notes"].append(note)
            elif entry.get("type") == "text":
                text = entry.get("text", "")
                # Full skill text is already represented above. Only remove a verified
                # prefix matching these frozen files, never arbitrary task text.
                if materialized and text.startswith(materialized):
                    text = text[len(materialized) :]
                item(request, "inputs", f"Input {index + 1}", text, event, key)
            else:
                item(request, "attachments", entry.get("type", "attachment"), entry, event, key)
        if params.get("outputSchema") is not None:
            item(request, "schema", "outputSchema", params["outputSchema"], event, "output_schema")
        return request

    def item(request, category, title, content, event, key, **extra):
        key = f"{category}:{key}"
        rendered = text_of(redact(content))
        value = {
            "id": f"{request['id']}:{key}",
            "category": category,
            "title": title,
            "content": rendered,
            "source_seq": event["seq"],
            "sha256": digest(rendered.encode()),
            "chars": len(rendered),
            "estimated_tokens": math.ceil(len(rendered) / 4),
            "measurement": "local_text_heuristic_chars_div_4",
            **extra,
        }
        if key not in request["_items"]:
            request["items"].append(value)
            request["_items"][key] = value
        else:
            request["_items"][key].update(value)

    def provider_item(request, value, event):
        if not isinstance(value, dict):
            return
        identity = value.get("id", f"event_{event['seq']}")
        kind = value.get("type")
        if kind == "agentMessage":
            if "text" in value:
                request["_output"][identity] = value["text"]
            item(
                request,
                "assistant",
                value.get("phase") or "assistant",
                request["_output"].get(identity, ""),
                event,
                identity,
            )
        elif kind == "reasoning":
            if value.get("summary"):
                item(
                    request,
                    "reasoning",
                    "Public reasoning summary",
                    value["summary"],
                    event,
                    identity,
                )
                # A final summary replaces the deltas for that item, rather than double-counting it.
                for key in list(request["_items"]):
                    if key.startswith(f"reasoning:{identity}:"):
                        old = request["_items"].pop(key)
                        request["items"].remove(old)
        elif kind in ("mcpToolCall", "commandExecution", "dynamicToolCall"):
            item(
                request,
                "tools",
                value.get("tool") or value.get("command") or kind,
                value,
                event,
                identity,
            )
        elif kind == "contextCompaction":
            compaction_event = {
                "seq": event["seq"],
                "kind": "compaction",
                "request_id": request["id"],
                "detail": (
                    "Public compaction event; removed content is not exposed "
                    "and full context is not reconstructed"
                ),
            }
            context_events.append(compaction_event)

    def record_usage(request, usage):
        if not isinstance(usage, dict):
            return
        total, last = counters(usage.get("total")), counters(usage.get("last"))
        baseline = request["_baseline"]
        delta = None
        if total is not None and baseline is not None:
            candidate = {
                key: total[key] - baseline[key]
                if total[key] is not None and baseline.get(key) is not None
                else None
                for key in USAGE_KEYS
            }
            if all(value is None or value >= 0 for value in candidate.values()):
                delta = candidate
        request["usage"] = {
            "source": "provider_reported",
            "thread_total": total,
            "last_model_request": last,
            "observed_turn_delta": delta,
            "context_window": usage.get("modelContextWindow"),
            "run_attribution": "known" if delta is not None else "unknown_baseline",
        }
        if total is not None and request.get("thread_id"):
            totals[request["thread_id"]] = total

    for event in sorted(events, key=lambda entry: entry["seq"]):
        kind = event["kind"]
        payload = event.get("payload", {})
        span = event.get("span_id")
        if kind == "call.started":
            call_spans.add(span)
            if payload.get("input_ref"):
                body, _ = read_text(payload["input_ref"])
                try:
                    params = json.loads(body or "null")
                except ValueError:
                    params = None
                if (
                    isinstance(params, dict)
                    and isinstance(params.get("turn"), dict)
                    and span not in raw_spans
                ):
                    make_request(event, params["turn"], "framework.input")
        if kind in ("call.completed", "call.failed", "call.incomplete"):
            if span in by_span and by_span[span]["status"] == "incomplete":
                by_span[span]["status"] = (
                    "completed" if kind == "call.completed" else kind.removeprefix("call.")
                )
        if kind == "provider.wire":
            message = payload.get("message", {})
            if not isinstance(message, dict):
                continue
            method, params = message.get("method"), message.get("params", {})
            if not isinstance(params, dict):
                continue
            if payload.get("direction") == "out":
                if method in ("thread/start", "thread/resume"):
                    instructions[span] = params
                    pending[(span, message.get("id"))] = (method, None)
                elif method == "turn/start":
                    request = make_request(event, params, "provider.wire")
                    pending[(span, message.get("id"))] = (method, request)
                    request_event = {
                        "seq": event["seq"],
                        "kind": "request",
                        "request_id": request["id"],
                        "detail": "App Server turn input submitted",
                    }
                    context_events.append(request_event)
                continue
            correlation = pending.pop((span, message.get("id")), None) if "id" in message else None
            if correlation:
                operation, request = correlation
                result = message.get("result", {})
                if operation == "thread/start" and isinstance(result, dict):
                    identifier = result.get("thread", {}).get("id")
                    if identifier:
                        fresh_threads.add(identifier)
                if operation == "turn/start" and request:
                    if "error" in message:
                        request["status"] = "failed"
                        request["notes"].append("Provider rejected turn/start")
                    else:
                        request["turn_id"] = result.get("turn", {}).get("id")
                        if request["turn_id"]:
                            by_turn[request["turn_id"]] = request
            request = by_turn.get(params.get("turnId")) or by_span.get(span)
            if not request:
                continue
            if (
                params.get("threadId")
                and request.get("thread_id")
                and params["threadId"] != request["thread_id"]
            ):
                continue
            if method == "turn/started":
                identifier = params.get("turn", {}).get("id")
                if identifier:
                    request["turn_id"] = identifier
                    by_turn[identifier] = request
            elif method in ("item/started", "item/completed"):
                provider_item(request, params.get("item"), event)
            elif method == "item/agentMessage/delta":
                identity = params.get("itemId", "unknown")
                request["_output"][identity] = request["_output"].get(identity, "") + params.get(
                    "delta", ""
                )
                item(
                    request, "assistant", "assistant", request["_output"][identity], event, identity
                )
            elif method == "item/reasoning/summaryTextDelta":
                identity = f"{params.get('itemId', 'unknown')}:{params.get('summaryIndex', 0)}"
                request["_summary"][identity] = request["_summary"].get(identity, "") + params.get(
                    "delta", ""
                )
                item(
                    request,
                    "reasoning",
                    "Public reasoning summary",
                    request["_summary"][identity],
                    event,
                    identity,
                )
            elif method == "thread/tokenUsage/updated":
                record_usage(request, params.get("tokenUsage"))
            elif method == "turn/completed":
                turn = params.get("turn", {})
                request["status"] = turn.get("status", "incomplete")
                for entry in turn.get("items", []):
                    provider_item(request, entry, event)
            elif method in ("thread/compacted", "error", "warning"):
                provider_event = {
                    "seq": event["seq"],
                    "kind": "compaction" if method == "thread/compacted" else method,
                    "request_id": request["id"],
                    "detail": text_of(params),
                }
                context_events.append(provider_event)
        elif span not in raw_spans and span in by_span:
            request = by_span[span]
            if kind == "ai.reasoning_summary":
                identity = payload.get("item_id") or "summary"
                request["_summary"][identity] = request["_summary"].get(identity, "") + payload.get(
                    "text", ""
                )
                item(
                    request,
                    "reasoning",
                    "Public reasoning summary",
                    request["_summary"][identity],
                    event,
                    identity,
                )
            elif kind == "ai.usage":
                record_usage(request, payload.get("usage"))
        if kind in (
            "decision.proposed",
            "action.intent",
            "action.result",
            "verification.unavailable",
            "run.state",
        ):
            workflow_event = {
                "seq": event["seq"],
                "kind": kind,
                "detail": text_of(payload),
                "span_id": span,
            }
            context_events.append(workflow_event)

    previous = {}
    for request in requests:
        composition = {
            key: {"items": 0, "chars": 0, "estimated_tokens": 0} for key in category_names
        }
        current = {}
        for entry in request["items"]:
            bucket = composition[entry["category"]]
            for key, value in (
                ("items", 1),
                ("chars", entry["chars"]),
                ("estimated_tokens", entry["estimated_tokens"]),
            ):
                bucket[key] += value
            current[(entry["category"], entry["title"], entry["sha256"])] = entry
        # Diff only submitted context, not outputs; different roles/threads have separate baselines.
        inputs = {
            key: value
            for key, value in current.items()
            if key[0] in ("skills", "inputs", "instructions", "schema", "attachments")
        }
        session = request.get("thread_id") or request.get("span_id")
        before = previous.get(session)
        request["diff"] = {
            "comparable": before is not None,
            "added": [
                entry["id"]
                for key, entry in inputs.items()
                if before is not None and key not in before
            ],
            "removed": [
                {
                    "category": entry["category"],
                    "title": entry["title"],
                    "sha256": entry["sha256"],
                }
                for key, entry in (before or {}).items()
                if key not in inputs
            ],
        }
        previous[session] = inputs
        request["composition"] = composition
        request["estimated_input_tokens"] = sum(
            composition[key]["estimated_tokens"]
            for key in ("instructions", "skills", "inputs", "schema", "attachments")
        )
        request["summary_available"] = any(
            entry["category"] == "reasoning" and entry["content"] for entry in request["items"]
        )
        request["notes"].append(
            "Only application-visible content is shown. A skill reference path does not prove "
            "it was read. Internal history and the complete compacted model context "
            "are not observable."
        )
        for key in list(request):
            if key.startswith("_"):
                del request[key]
    aggregate = {key: None for key in USAGE_KEYS}
    known = [
        request["usage"]["observed_turn_delta"]
        for request in requests
        if request["usage"] and request["usage"]["observed_turn_delta"] is not None
    ]
    for key in USAGE_KEYS:
        values = [value[key] for value in known if value[key] is not None]
        if values:
            aggregate[key] = sum(values)
    counts = Counter(event["kind"] for event in events)
    insights = {
        "version": 1,
        "run": dict(run),
        "categories": category_names,
        "requests": requests,
        "context_events": context_events,
        "summary": {
            "events": len(events),
            "calls": len(call_spans),
            "failed_calls": counts["call.failed"],
            "incomplete_calls": counts["call.incomplete"],
            "ai_turns": len(requests),
            "usage": aggregate,
            "usage_coverage": f"{len(known)}/{len(requests)}",
            "usage_complete": bool(requests) and len(known) == len(requests),
        },
        "visibility": {
            "boundary": "application_observed",
            "full_model_context": "unavailable",
            "internal_model_request_count": None,
            "cost": None,
        },
    }
    return insights


def build_insights(store, run_id):
    def read_text(relative):
        try:
            path = within(store.run_dir(run_id), relative)
            if path.stat().st_size > 1024 * 1024:
                preview_result = (
                    None,
                    f"{relative} exceeds the 1 MiB preview limit; the original file is retained",
                )
                return preview_result
            content = path.read_text(encoding="utf-8")
            # Only use pinned Skill content, and expose tampering as missing evidence.
            if relative.startswith("skills/"):
                manifest_path = store.run_dir(run_id) / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("files", {}).get(relative) != digest(path.read_bytes()):
                    preview_result = (None, f"{relative} does not match its frozen digest")
                    return preview_result
            preview_result = (text_of(redact(content)), None)
            return preview_result
        except (OSError, ValueError, FrameworkError):
            preview_result = (None, f"{relative} cannot be read")
            return preview_result

    limited = store.event_bytes(run_id) > 16 * 1024**2
    events = store.events(run_id, limit=2000 if limited else None)
    insights = project(events, store.run(run_id), read_text)
    insights["visibility"]["event_window"] = "latest_2000_events" if limited else "complete"
    return insights
