import copy
import json

import pytest

from game_automation.common import skill_prefix
from game_automation.observability import build_insights, export_trace
from game_automation.observability.insights import project


def ledger():
    events = []

    def emit(kind, payload, span="span_1"):
        events.append(
            {
                "seq": len(events) + 1,
                "kind": kind,
                "payload": payload,
                "span_id": span,
                "request_id": "request",
                "timestamp": "2026-10-21T00:00:00Z",
            }
        )

    def wire(direction, method=None, params=None, span="span_1", **kwargs):
        message = kwargs
        if method:
            message.update(method=method, params=params or {})
        emit("provider.wire", {"direction": direction, "message": message}, span)

    return events, emit, wire


def begin(wire, *, span="span_1", new=True, turn="turn_1", text="hello", skill=True):
    wire(
        "out",
        "thread/start" if new else "thread/resume",
        {"developerInstructions": "trusted"},
        span,
        id="start",
    )
    wire("in", span=span, id="start", result={"thread": {"id": "thread_1"}})
    inputs = [{"type": "text", "text": text}]
    if skill:
        inputs.insert(0, {"type": "skill", "name": "project-base", "path": "ignored-source-path"})
    wire(
        "out",
        "turn/start",
        {"threadId": "thread_1", "input": inputs, "outputSchema": {"type": "object"}},
        span,
        id="turn",
    )
    wire("in", span=span, id="turn", result={"turn": {"id": turn}})


def usage(wire, total, *, span="span_1", turn="turn_1"):
    value = {
        "inputTokens": total - 10,
        "outputTokens": 10,
        "totalTokens": total,
        "cachedInputTokens": 0,
        "reasoningOutputTokens": 2,
    }
    wire(
        "in",
        "thread/tokenUsage/updated",
        {
            "threadId": "thread_1",
            "turnId": turn,
            "tokenUsage": {"total": value, "last": {"totalTokens": 12}},
        },
        span,
    )


def fold(events):
    return project(events, {"id": "run"}, lambda relative: ("pinned skill", None))


def test_materialized_skill_text_is_visible_once_without_hiding_source_data():
    events, _, wire = ledger()
    prefix = skill_prefix([("project-base", "pinned skill")])
    begin(wire, text=prefix + "user task")
    result = fold(events)
    items = result["requests"][0]["items"]
    assert [i["content"] for i in items if i["category"] == "skills"] == ["pinned skill"]
    assert [i["content"] for i in items if i["category"] == "inputs"] == ["user task"]
    # A different body cannot be hidden by imitating the trusted wrapper.
    events, _, wire = ledger()
    mismatched = skill_prefix([("project-base", "untrusted source")])
    begin(wire, text=mismatched + "user task")
    items = fold(events)["requests"][0]["items"]
    assert [i["content"] for i in items if i["category"] == "inputs"] == [mismatched + "user task"]


def test_actual_usage_is_not_multiplied_by_updates_or_normalized_duplicates():
    events, emit, wire = ledger()
    begin(wire)
    usage(wire, 100)
    usage(wire, 100)
    emit("ai.usage", {"usage": {"total": {"totalTokens": 100}}})
    usage(wire, 140)
    begin(wire, span="span_2", new=False, turn="turn_2", text="changed")
    usage(wire, 200, span="span_2", turn="turn_2")
    result = fold(events)
    assert [r["usage"]["observed_turn_delta"]["totalTokens"] for r in result["requests"]] == [
        140,
        60,
    ]
    assert result["summary"]["usage"]["totalTokens"] == 200
    assert result["summary"]["usage_complete"]
    assert result["requests"][1]["diff"]["comparable"]
    assert len(result["requests"][1]["diff"]["added"]) == 1
    assert len(result["requests"][1]["diff"]["removed"]) == 1


def test_resumed_thread_with_missing_baseline_does_not_fabricate_run_usage():
    events, _, wire = ledger()
    begin(wire, new=False)
    usage(wire, 5000)
    result = fold(events)
    assert result["summary"]["usage"]["totalTokens"] is None
    assert result["requests"][0]["usage"]["last_model_request"]["totalTokens"] == 12
    assert result["requests"][0]["usage"]["run_attribution"] == "unknown_baseline"


def test_empty_usage_counters_are_unavailable_not_zero():
    events, _, wire = ledger()
    begin(wire)
    wire(
        "in",
        "thread/tokenUsage/updated",
        {"threadId": "thread_1", "tokenUsage": {"total": {}, "last": {}}},
    )
    result = fold(events)
    assert result["summary"]["usage"]["totalTokens"] is None
    assert not result["summary"]["usage_complete"]


def test_assistant_deltas_and_completed_item_are_one_message():
    events, _, wire = ledger()
    begin(wire)
    for text in ["hel", "lo"]:
        wire(
            "in", "item/agentMessage/delta", {"itemId": "answer", "delta": text, "turnId": "turn_1"}
        )
    wire(
        "in",
        "item/completed",
        {"turnId": "turn_1", "item": {"id": "answer", "type": "agentMessage", "text": "hello"}},
    )
    wire(
        "in",
        "turn/completed",
        {
            "turn": {
                "id": "turn_1",
                "status": "completed",
                "items": [{"id": "answer", "type": "agentMessage", "text": "hello"}],
            }
        },
    )
    result = fold(events)
    messages = [i for i in result["requests"][0]["items"] if i["category"] == "assistant"]
    assert len(messages) == 1 and messages[0]["content"] == "hello"
    assert not result["requests"][0]["summary_available"]


def test_summary_completion_replaces_delta_and_tools_stay_single_items():
    events, _, wire = ledger()
    begin(wire)
    wire(
        "in",
        "item/reasoning/summaryTextDelta",
        {"itemId": "reason", "delta": "public", "summaryIndex": 0},
    )
    wire(
        "in",
        "item/completed",
        {"item": {"id": "reason", "type": "reasoning", "summary": ["public"]}},
    )
    for method, status in [("item/started", "running"), ("item/completed", "completed")]:
        wire(
            "in",
            method,
            {"item": {"id": "tool", "type": "mcpToolCall", "tool": "observe", "status": status}},
        )
    result = fold(events)
    request = result["requests"][0]
    assert request["summary_available"]
    assert request["composition"]["reasoning"]["items"] == 1
    assert request["composition"]["tools"]["items"] == 1
    assert "completed" in next(i["content"] for i in request["items"] if i["category"] == "tools")


def test_projection_is_deterministic_does_not_mutate_events_or_claim_full_context():
    events, _, wire = ledger()
    begin(wire)
    previous = copy.deepcopy(events)
    one = fold(events)
    assert one == fold(events)
    assert events == previous
    assert one["visibility"]["full_model_context"] == "unavailable"
    assert one["visibility"]["cost"] is None
    assert one["summary"]["usage"]["totalTokens"] is None
    assert one["requests"][0]["status"] == "incomplete"


async def test_frozen_skill_browsing_and_offline_export_do_not_execute(store, run_id, host):
    from game_automation.kernel.workflow import execute

    await execute(store, run_id)
    old = (store.run_dir(run_id) / "skills/project-base/SKILL.md").read_text(encoding="utf-8")
    (store.root / "skills/project-base/SKILL.md").write_text(
        "changed live source", encoding="utf-8"
    )
    before = len(store.events(run_id))
    insights = build_insights(store, run_id)
    skills = [i for r in insights["requests"] for i in r["items"] if i["category"] == "skills"]
    assert any(i["content"] == old for i in skills)
    assert insights["summary"]["usage"]["totalTokens"] is None
    output = export_trace(store, run_id)
    assert output.with_name("context.json").is_file()
    assert json.loads(output.with_name("context.json").read_text(encoding="utf-8")) == insights
    assert len(store.events(run_id)) == before


@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_modified_skill_is_explicitly_unavailable(store, run_id, missing):
    from game_automation.kernel.workflow import execute

    await execute(store, run_id)
    path = store.run_dir(run_id) / "skills/project-base/SKILL.md"
    if missing:
        path.unlink()
    else:
        path.write_text("tampered", encoding="utf-8")
    view = build_insights(store, run_id)
    item = next(i for r in view["requests"] for i in r["items"] if i["title"] == "project-base")
    assert item["availability"] == "unavailable"
    assert view["requests"][0]["notes"]
