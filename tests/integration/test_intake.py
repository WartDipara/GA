import asyncio
import copy
import json

import pytest

from game_automation.cli import main
from game_automation.common import FrameworkError, json_text
from game_automation.contracts import schema
from game_automation.kernel.workflow import execute, new_run
from game_automation.kernel.workflow.intake import check_result, intake_status, read_document
from game_automation.observability import export_trace


@pytest.fixture
def scripted_ai(store):
    registry = store.root / "config/plugins.toml"
    text = (
        registry.read_text()
        .replace("[plugins.agent_codex]", "[plugins.intake_ai_fixture]")
        .replace(
            'manifest = "plugins/agent_codex/plugin.toml"',
            'manifest = "tests/fixtures/intake_ai/plugin.toml"',
        )
    )
    registry.write_text(text, encoding="utf-8")

    def respond(decision=None, **kwargs):
        path = store.root / "tests/fixtures/intake_ai/response.json"
        path.write_text(json_text({"decision": decision, **kwargs}), encoding="utf-8")

    return respond


def ready(target="com.example.game", source_id="submission"):
    defaults = {
        "platform": "android",
        "scope": ["all reachable interactive elements"],
        "limits": {"max_actions": 100, "timeout_seconds": 300},
        "constraints": [],
    }
    task = {"objective": "Inspect settings", "target": target, **defaults}
    return {
        "status": "ready",
        "task": task,
        "questions": [],
        "reason_summary": "Extracted the objective and supplied target; other fields use policy defaults.",
        "provenance": [
            {"field": "objective", "source_id": "submission", "quote": "Inspect settings"},
            {"field": "target", "source_id": source_id, "quote": target},
            *[
                {"field": field, "source_id": "policy", "quote": json.dumps(value)}
                for field, value in defaults.items()
            ],
        ],
    }


def missing():
    return {
        "status": "needs_input",
        "task": None,
        "questions": [{"field": "target", "question": "Which Android package should be tested?"}],
        "reason_summary": "The source does not specify a target.",
        "provenance": [],
    }


def document(store, text="Inspect settings in com.example.game"):
    return store.submit({"kind": "document", "filename": "task.md", "text": text})


async def test_canonical_intake_works_without_ai_and_never_rewrites(store, task):
    task["limits"]["timeout_seconds"] = 600
    path = store.root / "config/plugins.toml"
    path.write_text(
        path.read_text().replace(
            '[plugins.agent_codex]\nmanifest = "plugins/agent_codex/plugin.toml"\nenabled = true',
            '[plugins.agent_codex]\nmanifest = "plugins/agent_codex/plugin.toml"\nenabled = false',
        )
    )
    run = new_run(store, store.submit(task), "intake")
    result = await execute(store, run)
    assert result["status"] == "succeeded" and result["stage"] == "intake"
    assert intake_status(store, run)["task"] == task
    assert [
        e["payload"]["capability"] for e in store.events(run) if e["kind"] == "call.started"
    ] == ["task.intake"]


async def test_document_nested_chain_sources_skills_and_offline_view(store, scripted_ai):
    scripted_ai(ready())
    task_id = document(store)
    run = new_run(store, task_id, "intake")
    result = await execute(store, run)
    assert result["status"] == "succeeded"
    events = store.events(run)
    calls = [e for e in events if e["kind"] == "call.started"]
    assert [e["payload"]["capability"] for e in calls] == ["task.intake", "ai.decide"]
    assert calls[1]["parent_span_id"] == calls[0]["span_id"]
    ai_input = json.loads((store.run_dir(run) / calls[1]["payload"]["input_ref"]).read_text())
    assert ai_input["role"] == "intake"
    assert [s["name"] for s in ai_input["turn"]["input"] if s["type"] == "skill"] == [
        "project-base",
        "task-intake",
    ]
    assert "game-exploration" not in json.dumps(ai_input)
    accepted = next(e for e in events if e["kind"] == "intake.accepted")
    assert accepted["span_id"] == calls[0]["span_id"]
    assert accepted["payload"]["task"]["target"] == "com.example.game"
    assert store.task(task_id)["kind"] == "document"
    before = store.events(run)
    html = export_trace(store, run).read_text(encoding="utf-8")
    assert "intake.accepted" in html and "com.example.game" in html
    assert store.events(run) == before
    assert not any(e["kind"] == "action.intent" for e in events)


async def test_missing_information_resume_is_append_only_and_no_empty_retry(store, scripted_ai):
    scripted_ai(missing())
    task_id = document(store, "Inspect settings")
    run = new_run(store, task_id, "intake")
    assert (await execute(store, run))["status"] == "blocked"
    first = copy.deepcopy(intake_status(store, run))
    count = len([e for e in store.events(run) if e["kind"] == "call.started"])
    assert (await execute(store, run))["status"] == "blocked"
    assert len([e for e in store.events(run) if e["kind"] == "call.started"]) == count
    assert main(["--project", str(store.root), "provide", run, "--text", "com.example.game"]) == 0
    supplement = next(e for e in store.events(run) if e["kind"] == "task.supplement")
    scripted_ai(ready(source_id=supplement["event_id"]))
    assert (await execute(store, run))["status"] == "succeeded"
    assert first in [e["payload"] for e in store.events(run) if e["kind"] == "intake.needs_input"]
    assert intake_status(store, run)["revision"] != first["revision"]
    assert store.task(task_id)["text"] == "Inspect settings"
    assert len([e for e in store.events(run) if e["kind"] == "intake.accepted"]) == 1


async def test_game_resume_reuses_accepted_intake_and_blocks_next_stage(store, task):
    run = new_run(store, store.submit(task), "game")
    result = await execute(store, run)
    assert (result["status"], result["stage"]) == ("blocked", "environment")
    assert (await execute(store, run))["status"] == "blocked"
    assert len([e for e in store.events(run) if e["kind"] == "intake.accepted"]) == 1
    assert len([e for e in store.events(run) if e["kind"] == "call.started"]) == 1
    assert main(["--project", str(store.root), "provide", run, "--text", "Change the target"]) == 2


@pytest.mark.parametrize(
    "failure",
    [
        "invented_target",
        "fake_source",
        "budget",
        "missing_provenance",
        "contradictory_result",
        "invalid_shape",
    ],
)
async def test_invalid_extraction_never_accepts_or_dispatches(store, scripted_ai, failure):
    output = ready()
    if failure == "invented_target":
        output["task"]["target"] = "invented.package"
    elif failure == "fake_source":
        output["provenance"][0]["source_id"] = "not-supplied"
    elif failure == "budget":
        output["task"]["limits"]["max_actions"] = 1000
    elif failure == "missing_provenance":
        output["provenance"].pop()
    elif failure == "contradictory_result":
        output["questions"] = missing()["questions"]
    else:
        output = {"status": "ready"}
    scripted_ai(output)
    run = new_run(store, document(store), "intake")
    assert (await execute(store, run))["status"] == "failed"
    assert not any(e["kind"] in ("intake.accepted", "action.intent") for e in store.events(run))
    assert any(e["kind"] in ("intake.rejected", "call.failed") for e in store.events(run))


async def test_intake_process_crash_then_resume_preserves_failure(store, scripted_ai):
    scripted_ai(fault="crash")
    run = new_run(store, document(store), "intake")
    assert (await execute(store, run))["status"] == "blocked"
    failures = [e for e in store.events(run) if e["kind"] == "call.incomplete"]
    assert len(failures) == 2
    scripted_ai(ready())
    assert (await execute(store, run))["status"] == "succeeded"
    assert all(e in store.events(run) for e in failures)


async def test_intake_deadline_keeps_incomplete_calls_and_blocks(store, scripted_ai):
    config = store.root / "config/application.toml"
    config.write_text(
        config.read_text().replace(
            "[intake]\ntimeout_seconds = 300", "[intake]\ntimeout_seconds = 1"
        )
    )
    scripted_ai(missing(), delay=30)
    run = new_run(store, document(store), "intake")
    assert (await execute(store, run))["status"] == "blocked"
    events = store.events(run)
    assert events[-1]["payload"]["reason"]["code"] == "BUDGET_EXHAUSTED"
    assert any(e["kind"] == "call.incomplete" for e in events)
    assert not any(e["kind"] in ("intake.accepted", "action.intent") for e in events)


async def test_nested_ai_deadline_is_not_generic_call_timeout(store, scripted_ai):
    config = store.root / "config/application.toml"
    config.write_text(
        config.read_text().replace("call_timeout_seconds = 30", "call_timeout_seconds = 0.05")
    )
    scripted_ai(ready(), delay=0.1)
    run = new_run(store, document(store), "intake")
    assert (await execute(store, run))["status"] == "succeeded"


async def test_cancel_nested_intake_before_acceptance(store, scripted_ai):
    scripted_ai(ready(), delay=30)
    run = new_run(store, document(store), "intake")

    async def cancel():
        for _ in range(300):
            if any(
                e["kind"] == "call.started" and e["payload"]["capability"] == "ai.decide"
                for e in store.events(run)
            ):
                store.cancel(run)
                return
            await asyncio.sleep(0.02)
        pytest.fail("Nested call never started")

    operation, _ = await asyncio.gather(execute(store, run), cancel())
    assert operation["status"] == "cancelled"
    assert not any(e["kind"] in ("intake.accepted", "action.intent") for e in store.events(run))


def test_document_submission_freezes_redacted_unicode_input(store, capsys):
    path = store.root / "input.md"
    path.write_bytes("\ufeffInspect settings \u6e2c\u8a66\r\ntoken=private-value\r\n".encode())
    argv = ["--project", str(store.root), "submit", "--document", str(path), "--key", "stable"]
    assert main(argv) == 0
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["task_id"] == task_id
    path.write_text("Changed on disk", encoding="utf-8")
    saved = store.task(task_id)
    assert "\u6e2c\u8a66" in saved["text"] and "\r" not in saved["text"]
    assert "private-value" not in json.dumps(saved)
    assert main(argv) == 2
    assert "IDEMPOTENCY_CONFLICT" in capsys.readouterr().err


@pytest.mark.parametrize(
    "suffix,raw,code",
    [
        (".pdf", b"pdf", "UNSUPPORTED_DOCUMENT"),
        (".txt", b"\xff", "INVALID_ENCODING"),
        (".md", b" \r\n", "INVALID_DOCUMENT"),
        (".txt", b"\x00", "INVALID_DOCUMENT"),
        (".md", b"x" * 200001, "DOCUMENT_TOO_LARGE"),
    ],
    ids=["unsupported", "encoding", "empty", "null", "oversize"],
)
def test_unsupported_document_rejected_before_submission(store, suffix, raw, code):
    path = store.root / ("input" + suffix)
    path.write_bytes(raw)
    with pytest.raises(FrameworkError) as error:
        read_document(path)
    assert error.value.code == code


def test_embedded_task_schemas_match_authoritative_contract(store):
    task = schema(store.root / "contracts/v1", "task")
    task.pop("$schema")
    task.pop("$id")
    for name in ("intake-request", "intake-result"):
        assert schema(store.root / "contracts/v1", name)["properties"]["task"]["anyOf"][0] == task


def test_unknown_default_cannot_claim_source_authority(store):
    output = ready()
    output["provenance"][0] = {
        "field": "objective",
        "source_id": "policy",
        "quote": '"Inspect settings"',
    }
    with pytest.raises(FrameworkError, match="default attribution"):
        check_result(
            store.root / "contracts/v1",
            output,
            [{"source_id": "submission", "text": "Inspect settings in com.example.game"}],
            {"limits": {"max_actions": 100, "timeout_seconds": 300}},
            None,
        )


def test_supplement_transaction_rolls_back_when_event_cannot_persist(store, task, monkeypatch):
    import sqlite3

    run = new_run(store, store.submit(task), "intake")
    store.transition(run, "blocked")

    def fail_event(*args, **kwargs):
        raise sqlite3.OperationalError("Injected persistence failure")

    monkeypatch.setattr(store, "_event", fail_event)
    with pytest.raises(FrameworkError) as error:
        store.supplement(run, "Additional data")
    assert error.value.code == "TRACE_UNAVAILABLE"
    assert not store.db.in_transaction
    assert not any(e["kind"] == "task.supplement" for e in store.events(run))
