import json

import pytest

from game_automation.common import FrameworkError
from game_automation.kernel.capability_router import Router
from game_automation.kernel.plugin_host import Host
from game_automation.kernel.workflow import execute, new_run
from game_automation.prompting import load


async def test_resume_after_completed_action_does_not_repeat(store, run_id, host):
    from game_automation.kernel.workflow.runner import demo

    store.transition(run_id, "running")
    await demo(store, run_id, host)
    # Simulate a crash after result persistence, before final state publication.
    assert store.run(run_id)["status"] == "running"
    result = await execute(store, run_id)
    assert result["status"] == "succeeded"
    events = store.events(run_id)
    assert len([e for e in events if e["kind"] == "action.intent"]) == 1
    assert any(e["kind"] == "action.reused_record" for e in events)


async def test_resume_unknown_action_blocks_before_plugin(store, run_id):
    store.transition(run_id, "running")
    store.reserve_action(run_id, "unknown_click")
    result = await execute(store, run_id)
    assert result["status"] == "blocked"
    assert not any(e["kind"] == "call.started" for e in store.events(run_id))


async def test_crashed_open_span_becomes_incomplete(store, run_id):
    store.transition(run_id, "running")
    store.event(
        run_id,
        "call.started",
        {"capability": "fixture.act", "attempt": 1},
        span_id="lost_span",
        request_id="lost_request",
    )
    store.reserve_action(run_id, "lost_action")
    await execute(store, run_id)
    events = store.events(run_id, span="lost_span")
    assert [e["kind"] for e in events] == ["call.started", "call.incomplete"]
    assert events[-1]["payload"]["result"] == "unknown"


async def test_replace_plugin_without_kernel_changes(store, task):
    path = store.root / "tests/fixtures/plugin/main.py"
    code = path.read_text(encoding="utf-8").replace(
        '"echo": value', '"echo": {"replacement": value}'
    )
    path.write_text(code, encoding="utf-8")
    run = new_run(store, store.submit(task), "demo")
    manifest = load(store, run)
    manifest["plugins"].pop("agent_codex")
    host = Host(store, run, Router(manifest["plugins"]), manifest)
    result = await host.call("fixture.echo", {"test": 1})
    assert result["echo"] == {"replacement": {"test": 1}}


def test_conflicting_event_id_is_not_silently_discarded(store, run_id):
    store.event(run_id, "plugin.test", {"value": 1}, event_id="stable")
    with pytest.raises(FrameworkError) as error:
        store.event(run_id, "plugin.test", {"value": 2}, event_id="stable")
    assert error.value.code == "EVENT_ID_CONFLICT"


def test_new_source_file_invalidates_resume(store, run_id):
    (store.root / "src/game_automation/new_component.py").write_text("x = 1")
    with pytest.raises(FrameworkError) as error:
        load(store, run_id)
    assert error.value.code == "RUNTIME_CHANGED"


def test_secrets_in_common_key_styles_are_redacted(store, run_id):
    secrets = {
        "OPENAI_API_KEY": "sk-fixture",
        "accessToken": "access-fixture",
        "password": "pass-fixture",
    }
    store.event(run_id, "plugin.test", secrets)
    text = json.dumps(store.events(run_id))
    assert not any(secret in text for secret in secrets.values())
