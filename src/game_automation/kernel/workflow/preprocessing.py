import asyncio
import json
import os
from urllib.parse import urlsplit

from game_automation.common import FrameworkError, digest, uid, within
from game_automation.contracts import validate
from game_automation.errors import ErrorCode

from .capacity import admit_apk_batch


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
    span_items = {}
    active_spans = {}
    legacy_index = None
    item_events = {
        "preprocessing.apk.queued",
        "preprocessing.apk.started",
        "preprocessing.apk.ready",
        "preprocessing.apk.blocked",
    }
    for event in store.events(run_id):
        kind = event["kind"]
        span_id = event["span_id"]
        if kind == "run.state" and event["payload"]["to"] in ("blocked", "failed", "cancelled"):
            state = event["payload"]
            for item in items.values():
                if item["status"] == "running":
                    item["status"] = "incomplete"
                    item["error"] = state.get("reason")
                elif item["status"] == "queued" and state["to"] == "cancelled":
                    item["status"] = "cancelled"
            continue
        if kind in item_events:
            item = dict(event["payload"])
            index = item["index"]
            item["span_id"] = span_id
            items[index] = item
            if span_id:
                span_items[span_id] = index
                active_spans[index] = span_id
            elif kind == "preprocessing.apk.started":
                legacy_index = index
        elif (
            kind == "call.started"
            and event["payload"]["capability"] == "apk.prepare"
            and legacy_index is not None
        ):
            # Older sequential runs did not reserve an item span before dispatch.
            span_items[span_id] = legacy_index
            active_spans[legacy_index] = span_id
            items[legacy_index]["span_id"] = span_id
            legacy_index = None
        if span_id not in span_items:
            continue
        index = span_items[span_id]
        item = items[index]
        if active_spans[index] != span_id or item["status"] != "running":
            continue
        if kind == "plugin.preprocessing.download.progress":
            item["progress"] = event["payload"]
        elif kind in ("call.failed", "call.incomplete"):
            item["status"] = "incomplete" if kind == "call.incomplete" else "blocked"
            item["error"] = event["payload"]["error"]
    results = [items[index] for index in sorted(items)]
    return results


async def prepare_apk(store, run_id, host, settings, source_file, item, span_id):
    workspace_ref = item["workspace_ref"]
    workspace = within(store.run_dir(run_id), workspace_ref)
    aapt2 = os.environ.get("GAME_AUTO_AAPT2") or os.environ.get("AAPT2_CMD") or None
    request = {
        "source_file": str(source_file.resolve()),
        "source_index": item["index"],
        "source_id": item["source_id"],
        "workspace": str(workspace),
        "aapt2_executable": aapt2,
        "request_timeout_seconds": settings["request_timeout_seconds"],
        "max_attempts": settings["max_attempts"],
        "max_apk_bytes": settings["max_apk_bytes"],
        "download_budget_bytes": item["download_budget_bytes"],
        "disk_reserve_bytes": item["disk_reserve_bytes"],
    }
    validate(store.run_dir(run_id) / "contracts", "apk-prepare-request", request)
    item["status"] = "running"
    store.event(run_id, "preprocessing.apk.started", item, span_id=span_id)
    try:
        metadata = await host.call(
            "apk.prepare", request, timeout=settings["apk_timeout_seconds"], span_id=span_id
        )
    except FrameworkError as exc:
        if exc.code in (
            ErrorCode.TRACE_UNAVAILABLE,
            ErrorCode.CANCELLED,
            ErrorCode.CLOSED,
            ErrorCode.DISK_SPACE_INSUFFICIENT,
            ErrorCode.DISK_SPACE_UNAVAILABLE,
            ErrorCode.DISK_BUDGET_EXCEEDED,
        ):
            raise
        item["status"] = "blocked"
        item["error"] = exc.as_dict()
        kind = "preprocessing.apk.blocked"
    else:
        metadata_ref = store.artifact(run_id, f"{workspace_ref}/metadata.json", metadata)
        item["status"] = "ready"
        item["metadata"] = metadata
        item["metadata_ref"] = metadata_ref
        item["apk_ref"] = f"{workspace_ref}/application.apk"
        kind = "preprocessing.apk.ready"
    store.event(run_id, kind, item, span_id=span_id)
    return item["status"] == "blocked"


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
    worker_count = settings["download_workers"]
    if type(worker_count) is not int or not 1 <= worker_count <= 5:
        raise FrameworkError(ErrorCode.INVALID_DOWNLOAD_WORKERS)
    plans = await admit_apk_batch(
        store, run_id, host, manifest, submission, source_file, worker_count
    )
    reserve_bytes = manifest["application"]["storage"]["reserve_bytes"]
    queue = asyncio.Queue()
    for index, source_id in enumerate(submission["source_ids"]):
        host.check_cancel()
        span_id = uid("span")
        item = {
            "index": index,
            "source_id": source_id,
            "workspace_ref": f"apks/apk_{index + 1:04d}",
            "status": "queued",
            "download_budget_bytes": plans[index]["budget_bytes"],
            "disk_reserve_bytes": reserve_bytes,
        }
        store.event(run_id, "preprocessing.apk.queued", item, span_id=span_id)
        entry = (item, span_id)
        queue.put_nowait(entry)
    worker_count = min(worker_count, queue.qsize())
    queue_details = {"workers": worker_count, "items": queue.qsize()}
    store.event(run_id, "preprocessing.queue.started", queue_details)
    blocked = False

    async def worker():
        nonlocal blocked
        try:
            while True:
                host.check_cancel()
                try:
                    item, span_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if await prepare_apk(store, run_id, host, settings, source_file, item, span_id):
                    blocked = True
        except BaseException as exc:
            host.stop(exc)
            raise

    try:
        async with asyncio.TaskGroup() as workers:
            for index in range(worker_count):
                workers.create_task(worker(), name=f"apk-worker-{index + 1}")
    except ExceptionGroup as failures:
        error = host.failure if host.failure is not None else failures.exceptions[0]
        raise error
    summary = {"items": preprocessing_status(store, run_id)}
    summary_ref = store.artifact(run_id, "artifacts/preprocessing.json", summary)
    completed = {"summary_ref": summary_ref, "blocked": blocked}
    store.event(run_id, "preprocessing.completed", completed)
    if blocked:
        raise FrameworkError(ErrorCode.PREPROCESSING_BLOCKED)
