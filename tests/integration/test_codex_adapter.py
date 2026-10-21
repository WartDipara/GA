import importlib.util
import sys
from pathlib import Path

import pytest

from game_automation.common import FrameworkError
from game_automation.contracts import schema, validate
from game_automation.plugin_sdk.rpc import spawn
from game_automation.prompting import build_input

ROOT = Path(__file__).resolve().parents[2]


def adapter_module():
    spec = importlib.util.spec_from_file_location(
        "codex_adapter_under_test", ROOT / "plugins/agent_codex/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordedHost:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id

    async def request(self, method, payload):
        assert method == "host.event"
        self.store.event(
            self.run_id, payload["kind"], payload["payload"], event_id=payload["event_id"]
        )
        return {"persisted": True}


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("summary", None),
        ("no-summary", None),
        ("interrupted", "PROCESS_EXITED"),
        ("invalid", "AI_OUTPUT_INVALID"),
        ("quota", "AI_UNAVAILABLE"),
    ],
)
async def test_codex_protocol(store, run_id, monkeypatch, scenario, expected):
    module = adapter_module()

    async def fake_spawn(command, **kwargs):
        return await spawn(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "tests/fixtures/fake_appserver.py"),
                scenario,
                *command[1:],
            ],
            **kwargs,
        )

    monkeypatch.setattr(module, "spawn", fake_spawn)
    workspace = store.run_dir(run_id) / "workspace"
    workspace.mkdir()
    output_schema = schema(store.run_dir(run_id) / "contracts", "decision")
    params = {
        "codex": {"executable": sys.executable, "version": "0.162.1", "timeout_seconds": 5},
        "workspace": str(workspace),
        "role": "exploration",
        "thread_id": None,
        "turn": build_input(store, run_id, "exploration", {}, output_schema),
    }
    adapter = module.CodexAdapter(RecordedHost(store, run_id))
    if expected:
        with pytest.raises(FrameworkError) as error:
            await adapter.invoke("ai.decide", params)
        assert error.value.code == expected
    else:
        result = await adapter.invoke("ai.decide", params)
        validate(store.run_dir(run_id) / "contracts", "decision", result["decision"])
        assert "x" not in result["decision"]["proposed_action"]
        assert result["reasoning_summary_available"] == (scenario == "summary")
        assert result["usage"] is None
    events = store.events(run_id)
    assert any(e["kind"] == "ai.session" for e in events)
    assert any(
        e["kind"] == "provider.wire" and e["payload"]["message"].get("method") == "turn/start"
        for e in events
    )
    if scenario == "interrupted":
        assert any(e["kind"] == "ai.reasoning_summary" for e in events)
        assert not any(e["kind"] == "ai.summary_availability" for e in events)
