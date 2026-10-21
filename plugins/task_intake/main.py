"""Independent intake capability; no database or device access."""

import asyncio
import json
import tomllib
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


def factory(peer):
    async def handler(method, params):
        if method == "initialize":
            return {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
        if method == "health":
            return {"ok": True}
        if method != "invoke" or params["capability"] != "task.intake":
            raise FrameworkError("METHOD_NOT_FOUND", method)
        value = params["input"]
        if value["task"] is not None:
            task = value["task"]
            return {
                "status": "ready",
                "task": task,
                "questions": [],
                "reason_summary": "The submitted task is already in canonical form.",
                "provenance": [
                    {
                        "field": field,
                        "source_id": "submission",
                        "quote": json.dumps(content, ensure_ascii=False),
                    }
                    for field, content in task.items()
                ],
            }
        if value["ai"] is None:
            raise FrameworkError("INVALID_CONTRACT", "Document intake requires an AI request")
        result = await peer.request(
            "host.call",
            {"capability": "ai.decide", "input": value["ai"]},
            timeout=value["ai"]["codex"]["timeout_seconds"] + 45,
        )
        return result["decision"]

    return handler


if __name__ == "__main__":
    asyncio.run(serve(factory))
