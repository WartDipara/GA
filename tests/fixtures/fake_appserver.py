"""Offline provider protocol fixture; no network, model or device access."""

import json
import sys
from pathlib import Path

scenario = sys.argv[1]
if "--version" in sys.argv:
    print("codex-cli 0.162.1")
    raise SystemExit(0)


def send(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        send({"id": message["id"], "result": {"userAgent": "fixture-codex"}})
    elif method == "config/read":
        send({"id": message["id"], "result": {"config": {"mcp_servers": {"fixture-server": {}}}}})
    elif method in ("thread/start", "thread/resume"):
        assert params["config"]["mcp_servers.fixture-server.enabled"] is False
        assert params["config"]["features.shell_tool"] is False
        assert params["sandbox"] == "read-only"
        send({"id": message["id"], "result": {"thread": {"id": "thread_fixture"}}})
    elif method == "turn/start":
        skills = [i for i in params["input"] if i["type"] == "skill"]
        assert {i["name"] for i in skills} == {"project-base", "game-exploration"}
        assert all(Path(i["path"]).is_file() for i in skills)
        fields = params["outputSchema"]["properties"]["proposed_action"]
        assert set(fields["required"]) == set(fields["properties"])
        send({"id": message["id"], "result": {"turn": {"id": "turn_fixture"}}})
        send(
            {
                "method": "turn/started",
                "params": {"threadId": "thread_fixture", "turn": {"id": "turn_fixture"}},
            }
        )
        if scenario in ("summary", "interrupted"):
            send(
                {
                    "method": "item/reasoning/summaryTextDelta",
                    "params": {
                        "threadId": "thread_fixture",
                        "turnId": "turn_fixture",
                        "itemId": "reasoning_1",
                        "delta": "Fixture summary only.",
                    },
                }
            )
        if scenario == "interrupted":
            raise SystemExit(5)
        if scenario == "quota":
            send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread_fixture",
                        "turn": {
                            "id": "turn_fixture",
                            "status": "failed",
                            "error": {"message": "usage limit reached"},
                        },
                    },
                }
            )
            continue
        output = {
            "observation_id": "obs_fixture",
            "evidence_refs": ["artifacts/fixture.svg"],
            "objective": "Check",
            "reason_summary": "Fixture result",
            "expected_outcome": "Stop",
            "uncertainties": [],
            "proposed_action": {
                "id": "action_1",
                "type": "stop",
                "observation_id": "obs_fixture",
                "session_id": "session_fixture",
                "x": None,
                "y": None,
                "target_ref": None,
            },
        }
        text = "not json" if scenario == "invalid" else json.dumps(output)
        send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread_fixture",
                    "item": {"type": "agentMessage", "id": "message_1", "text": text},
                },
            }
        )
        send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread_fixture",
                    "turn": {"id": "turn_fixture", "status": "completed", "items": []},
                },
            }
        )
    elif method == "turn/interrupt":
        send({"id": message["id"], "result": {}})
