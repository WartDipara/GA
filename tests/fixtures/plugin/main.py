"""Artificial transport fixture. NEVER reports actual game/device coverage."""

import asyncio
import os
import sys
import tomllib
from pathlib import Path

from game_automation.common import FrameworkError
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text())


def factory(peer):
    async def handler(method, params):
        if method == "initialize":
            return {key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")}
        if method == "health":
            return {"ok": True}
        if method != "invoke":
            raise FrameworkError("METHOD_NOT_FOUND", method)
        capability, value = params["capability"], params["input"]
        if capability == "fixture.echo":
            return {"fixture": True, "echo": value}
        if capability == "fixture.decide":
            await peer.request(
                "host.event", {"kind": "ai.summary_availability", "payload": {"available": False}}
            )
            return {"fixture": True, "decision": value["decision"], "usage": None}
        if capability == "fixture.act":
            if value.get("fault") == "crash":
                os._exit(4)
            return {
                "action_id": value["action"]["id"],
                "status": "executed",
                "verified": False,
                "evidence_refs": [],
                "fixture": True,
            }
        if capability == "fixture.fail":
            raise FrameworkError("FIXTURE_TEMPORARY", "Injected failure", retryable=True)
        if capability == "fixture.slow":
            await asyncio.sleep(30)
        if capability == "fixture.crash":
            os._exit(3)
        if capability == "fixture.corrupt":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
            await asyncio.sleep(30)
        if capability == "fixture.nested":
            return await peer.request("host.call", {"capability": "fixture.echo", "input": value})
        raise FrameworkError("METHOD_NOT_FOUND", capability)

    return handler


if __name__ == "__main__":
    asyncio.run(serve(factory))
