import json
import os
from urllib.parse import urlsplit

from game_automation.common import FrameworkError, digest, within
from game_automation.contracts import validate
from game_automation.errors import ErrorCode


def read_batch(source_file, contracts):
    source_file = source_file.resolve()
    if source_file.stat().st_size > 4 * 1024 * 1024:
        raise FrameworkError(ErrorCode.APK_INPUT_TOO_LARGE)
    batch = json.loads(source_file.read_text(encoding="utf-8-sig"))
    validate(contracts, "apk-batch", batch)
    source_ids = []
    for url in batch["apkUrls"]:
        try:
            parsed = urlsplit(url)
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or parsed.port == 0
            ):
                raise ValueError
            if any(ord(character) < 32 or character.isspace() for character in url):
                raise ValueError
        except ValueError as exc:
            raise FrameworkError(ErrorCode.INVALID_APK_URL) from exc
        source_ids.append(digest(url.encode("utf-8")))
    submission = {"kind": "apk_batch", "source_ids": source_ids}
    return submission


def preprocessing_status(store, run_id):
    items = {}
    active_index = None
    for event in store.events(run_id):
        if event["kind"] in (
            "preprocessing.apk.started",
            "preprocessing.apk.ready",
            "preprocessing.apk.blocked",
        ):
            item = dict(event["payload"])
            items[item["index"]] = item
            active_index = item["index"]
        elif active_index is not None and items[active_index]["status"] == "running":
            item = items[active_index]
            if event["kind"] == "plugin.preprocessing.download.progress":
                item["progress"] = event["payload"]
            elif event["kind"] in ("call.failed", "call.incomplete"):
                item["status"] = "incomplete" if event["kind"] == "call.incomplete" else "blocked"
                item["error"] = event["payload"]["error"]
    results = [items[index] for index in sorted(items)]
    return results


async def preprocess(store, run_id, host, manifest, submission, source_file):
    contracts = store.run_dir(run_id) / "contracts"
    if source_file is None:
        raise FrameworkError(ErrorCode.APK_INPUT_REQUIRED)
    try:
        current = read_batch(source_file, contracts)
    except (OSError, ValueError, FrameworkError) as exc:
        raise FrameworkError(ErrorCode.APK_INPUT_REQUIRED) from exc
    if current != submission:
        raise FrameworkError(
            ErrorCode.APK_SOURCE_CHANGED,
            "APK list differs from this batch; use the original list or create a new batch",
        )
    settings = manifest["application"]["preprocessing"]
    blocked = False
    for index, source_id in enumerate(submission["source_ids"]):
        host.check_cancel()
        workspace_ref = f"apks/apk_{index + 1:04d}"
        workspace = within(store.run_dir(run_id), workspace_ref)
        request = {
            "source_file": str(source_file.resolve()),
            "source_index": index,
            "source_id": source_id,
            "workspace": str(workspace),
            "aapt2_executable": os.environ.get("GAME_AUTO_AAPT2")
            or os.environ.get("AAPT2_CMD")
            or None,
            "request_timeout_seconds": settings["request_timeout_seconds"],
            "max_attempts": settings["max_attempts"],
            "max_apk_bytes": settings["max_apk_bytes"],
        }
        validate(contracts, "apk-prepare-request", request)
        item = {
            "index": index,
            "source_id": source_id,
            "workspace_ref": workspace_ref,
            "status": "running",
        }
        store.event(run_id, "preprocessing.apk.started", item)
        start_seq = store.events(run_id)[-1]["seq"]
        try:
            metadata = await host.call(
                "apk.prepare", request, timeout=settings["apk_timeout_seconds"]
            )
        except FrameworkError as exc:
            if exc.code in (ErrorCode.TRACE_UNAVAILABLE, ErrorCode.CANCELLED, ErrorCode.CLOSED):
                raise
            item["status"] = "blocked"
            item["error"] = exc.as_dict()
            blocked = True
            kind = "preprocessing.apk.blocked"
        else:
            metadata_ref = store.artifact(run_id, f"{workspace_ref}/metadata.json", metadata)
            item["status"] = "ready"
            item["metadata"] = metadata
            item["metadata_ref"] = metadata_ref
            item["apk_ref"] = f"{workspace_ref}/application.apk"
            kind = "preprocessing.apk.ready"
        call = next(
            (
                event
                for event in reversed(store.events(run_id))
                if event["kind"] == "call.started"
                and event["payload"]["capability"] == "apk.prepare"
                and event["seq"] > start_seq
            ),
            None,
        )
        trace_ids = (
            {key: call[key] for key in ("span_id", "parent_span_id", "request_id")} if call else {}
        )
        store.event(run_id, kind, item, **trace_ids)
    summary = {"items": preprocessing_status(store, run_id)}
    summary_ref = store.artifact(run_id, "artifacts/preprocessing.json", summary)
    store.event(run_id, "preprocessing.completed", {"summary_ref": summary_ref, "blocked": blocked})
    if blocked:
        raise FrameworkError(ErrorCode.PREPROCESSING_BLOCKED)
