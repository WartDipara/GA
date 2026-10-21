"""Scripted intake AI transport fixture. Does not interpret text or call a model."""

import asyncio
import json
import os
import tomllib
from pathlib import Path

from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text())


def factory(peer):
    async def handler(method, params):
        if method == "initialize":
            return {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
        if method == "health":
            return {"ok": True}
        response = json.loads(Path(__file__).with_name("response.json").read_text(encoding="utf-8"))
        if response.get("fault") == "crash":
            os._exit(5)
        await asyncio.sleep(response.get("delay", 0))
        await peer.request(
            "host.event", {"kind": "ai.summary_availability", "payload": {"available": False}}
        )
        return {"decision": response.get("decision"), "usage": None}

    return handler


if __name__ == "__main__":
    asyncio.run(serve(factory))
