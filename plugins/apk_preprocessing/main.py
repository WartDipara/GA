"""Download and inspect one APK inside its assigned filesystem workspace."""

import asyncio
import json
import os
import tomllib
from pathlib import Path

import httpx
from download import Downloader
from metadata import inspect_apk

from game_automation.common import FrameworkError, digest, within
from game_automation.errors import ErrorCode
from game_automation.plugin_sdk.rpc import serve

MANIFEST = tomllib.loads(Path(__file__).with_name("plugin.toml").read_text(encoding="utf-8"))


def read_source(request):
    try:
        source_file = Path(request["source_file"])
        if source_file.stat().st_size > 4 * 1024 * 1024:
            raise ValueError
        batch = json.loads(source_file.read_text(encoding="utf-8-sig"))
        url = batch["apkUrls"][request["source_index"]]
        if not isinstance(url, str) or digest(url.encode("utf-8")) != request["source_id"]:
            raise ValueError
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
            raise ValueError
    except (OSError, ValueError, KeyError, IndexError, TypeError, httpx.InvalidURL) as exc:
        raise FrameworkError(ErrorCode.APK_SOURCE_CHANGED) from exc
    return url


def create_handler(peer):
    async def event(kind, payload):
        event_request = {"kind": "plugin.preprocessing." + kind, "payload": payload}
        acknowledgement = await peer.request("host.event", event_request)
        if acknowledgement != {"persisted": True}:
            raise FrameworkError(ErrorCode.TRACE_UNAVAILABLE)

    async def handler(method, params):
        if method == "initialize":
            response = {
                key: MANIFEST[key] for key in ("protocol_version", "version", "capabilities")
            }
            return response
        if method == "health":
            response = {"ok": True}
            return response
        if method != "invoke" or params["capability"] != "apk.prepare":
            raise FrameworkError(ErrorCode.METHOD_NOT_FOUND, method)
        request = params["input"]
        url = read_source(request)
        workspace = Path(request["workspace"]).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        work = within(workspace, "work")
        work.mkdir(exist_ok=True)
        temporary = within(work, "tmp")
        temporary.mkdir(exist_ok=True)
        for name in ("TEMP", "TMP", "TMPDIR"):
            os.environ[name] = str(temporary)
        downloader = Downloader(workspace, request, event)
        apk = await downloader.run(url)
        metadata = await inspect_apk(apk, request["aapt2_executable"], event)
        metadata["sizeBytes"] = apk.stat().st_size
        metadata["sha256"] = downloader.state["prefix_sha256"]
        return metadata

    return handler


if __name__ == "__main__":
    asyncio.run(serve(create_handler))
