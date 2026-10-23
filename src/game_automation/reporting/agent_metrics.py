"""Metrics with explicit denominators and immutable implementation fingerprints."""

import json
import math
from collections import defaultdict

from game_automation.common import digest


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def version_identity(store, run_id):
    path = store.run_dir(run_id) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    code = sorted(manifest.get("source_hashes", {}).values())
    skills = {
        key: value for key, value in manifest.get("files", {}).items() if key.startswith("skills/")
    }
    identity = {
        "framework_version": manifest.get("framework_version"),
        "code_fingerprint": digest(json.dumps(code).encode()) if code else None,
        "skill_fingerprint": digest(json.dumps(skills, sort_keys=True).encode())
        if skills
        else None,
    }
    return identity


def trace_metrics(spans, actor_ref=None):
    groups = defaultdict(list)
    for span in spans:
        if span["kind"] not in ("chat", "tool"):
            continue
        attributes = span["attributes"]
        key = (
            attributes.get("gen_ai.agent.name"),
            attributes.get("gen_ai.request.model"),
            actor_ref,
        )
        groups[key].append(span)
    rows = []
    for (agent, model, actor), calls in groups.items():
        timed = [span["duration_ms"] for span in calls if span["duration_ms"] is not None]
        errors = sum(span["status"] == "error" for span in calls)
        closed = sum(span["status"] in ("ok", "error") for span in calls)
        tokens = [
            span.get("usage", {}).get("attributed") for span in calls if span["kind"] == "chat"
        ]
        token_values = [value for value in tokens if value is not None]
        estimates = [
            span.get("cost", {}).get("estimate_usd") for span in calls if span["kind"] == "chat"
        ]
        known_estimates = [value for value in estimates if value is not None]
        row = {
            "agent": agent,
            "model": model,
            "actor_ref": actor,
            "calls": len(calls),
            "errors": errors,
            "error_rate": errors / closed if closed else None,
            "error_rate_denominator": closed,
            "incomplete": sum(span["status"] == "incomplete" for span in calls),
            "latency_p50_ms": percentile(timed, 0.5),
            "latency_p95_ms": percentile(timed, 0.95),
            "timing_coverage": f"{len(timed)}/{len(calls)}",
            "attributed_tokens": {
                key: sum(value[key] for value in token_values if value.get(key) is not None)
                if any(value.get(key) is not None for value in token_values)
                else None
                for key in ("inputTokens", "cachedInputTokens", "outputTokens", "totalTokens")
            },
            "usage_coverage": f"{len(token_values)}/{len(tokens)}",
            "api_reference_estimate_usd": sum(known_estimates) if known_estimates else None,
            "cost_coverage": f"{len(known_estimates)}/{len(estimates)}",
            "cost_complete": bool(estimates) and len(known_estimates) == len(estimates),
        }
        rows.append(row)
    metrics = {
        "groups": rows,
        "error_rate_basis": "Failed / (completed + failed) calls; incomplete calls are separate",
        "latency_basis": (
            "Nearest-rank percentiles of measured calls; nested durations are not summed"
        ),
        "usage_basis": "Provider session-counter deltas; unknown baselines are excluded",
        "cost_basis": "Partial OpenAI API reference estimate, not subscription or invoice cost",
    }
    return metrics
