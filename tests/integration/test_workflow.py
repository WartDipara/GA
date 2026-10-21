import json

import pytest

from game_automation.common import FrameworkError
from game_automation.contracts import validate
from game_automation.kernel.policy import ExecutionLock
from game_automation.kernel.workflow import execute, new_run
from game_automation.observability import export_trace
from game_automation.prompting import build_input, load


async def test_demo_full_chain_and_offline_export(store, run_id):
    result = await execute(store, run_id)
    assert result["status"] == "succeeded"
    events = store.events(run_id)
    kinds = [e["kind"] for e in events]
    assert kinds.count("action.intent") == kinds.count("action.result") == 1
    assert "decision.proposed" in kinds
    assert "verification.unavailable" in kinds
    assert any(e["parent_span_id"] for e in events)
    assert any(
        e["kind"] == "ai.summary_availability" and not e["payload"]["available"] for e in events
    )
    for e in events:
        validate(store.root / "contracts/v1", "trace", {k: v for k, v in e.items() if k != "seq"})
    before = len(events)
    output = export_trace(store, run_id)
    text = output.read_text(encoding="utf-8")
    assert "fixture.act" in text and "decision.proposed" in text
    assert len(store.events(run_id)) == before
    with pytest.raises(FrameworkError, match="terminal"):
        await execute(store, run_id)
    assert (
        len(
            [
                e
                for e in store.events(run_id)
                if e["kind"] == "run.state" and e["payload"]["to"] == "succeeded"
            ]
        )
        == 1
    )


def test_submission_identity(store, task):
    first = store.submit(task, "same")
    assert store.submit(task, "same") == first
    with pytest.raises(FrameworkError, match="another task"):
        store.submit({**task, "objective": "changed"}, "same")


def test_skill_snapshot_and_new_batch(store, run_id):
    path = store.root / "skills/game-exploration/SKILL.md"
    old = (store.run_dir(run_id) / "skills/game-exploration/SKILL.md").read_text(encoding="utf-8")
    path.write_text(old + "\nA new test instruction.\n", encoding="utf-8")
    turn = build_input(store, run_id, "exploration", {"input": "test"}, {"type": "object"})
    assert [i["name"] for i in turn["input"] if i["type"] == "skill"] == [
        "project-base",
        "game-exploration",
    ]
    assert (store.run_dir(run_id) / "skills/game-exploration/SKILL.md").read_text(
        encoding="utf-8"
    ) == old
    new = new_run(store, store.run(run_id)["task_id"], "demo")
    assert load(store, new)["files"] != load(store, run_id)["files"]


async def test_missing_skill_blocks_before_provider(store, task):
    (store.root / "skills/project-base/SKILL.md").unlink()
    run = new_run(store, store.submit(task), "demo")
    assert store.run(run)["status"] == "blocked"
    await execute(store, run)
    assert not any(e["kind"] == "call.started" for e in store.events(run))


def test_tampered_snapshot_rejected(store, run_id):
    (store.run_dir(run_id) / "skills/project-base/SKILL.md").write_text("changed")
    with pytest.raises(FrameworkError, match="Frozen resource changed"):
        load(store, run_id)


async def test_unimplemented_game_is_blocked(store, task):
    run = new_run(store, store.submit(task), "game")
    result = await execute(store, run)
    assert result["status"] == "blocked"
    assert "MISSING_CAPABILITY" in json.dumps(store.events(run))
    assert (store.run_dir(run) / "reports/trace.html").is_file()


def test_single_execution_lock(store):
    with ExecutionLock(store.var / "execution.lock"):
        with pytest.raises(FrameworkError, match="Another local execution"):
            with ExecutionLock(store.var / "execution.lock"):
                pytest.fail("Acquired lock twice")


def test_dedup_and_redaction_and_html_escape(store, run_id):
    payload = {
        "password": "password-secret",
        "text": "token=token-secret <script>alert(1)</script>",
        "nested": json.dumps({"api_key": "api-secret"}),
    }
    for _ in range(2):
        store.event(run_id, "plugin.test", payload, event_id="duplicate")
    saved = [e for e in store.events(run_id) if e["event_id"] == "duplicate"]
    assert len(saved) == 1
    text = json.dumps(saved)
    assert all(s not in text for s in ("password-secret", "token-secret", "api-secret"))
    html = export_trace(store, run_id).read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


async def test_cancel_before_execution(store, run_id):
    store.cancel(run_id)
    result = await execute(store, run_id)
    assert result["status"] == "cancelled"
    assert not any(e["kind"] == "call.started" for e in store.events(run_id))
