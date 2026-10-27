"""Metadata-only OTLP/JSON export; no network exporter or second event writer."""

import gzip
import hashlib
import json
import math
import os
import re
import tempfile
import tomllib
from datetime import datetime, timezone

from game_automation.common import FrameworkError, atomic_json
from game_automation.contracts import validate
from game_automation.disk import require_disk_space
from game_automation.errors import ErrorCode
from game_automation.storage.trace_retention import settings

from .trace_projection import project_trace

MAPPING_VERSION = "1"
SAFE_ATTRIBUTES = {
    "gen_ai.operation.name",
    "gen_ai.agent.name",
    "gen_ai.request.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.reasoning_output_tokens",
    "game_automation.attempt_id",
    "game_automation.duration_scope",
    "error.type",
}


def identifier(run_id, span_id=None):
    source = f"game-automation:otlp:{MAPPING_VERSION}:{run_id}"
    if span_id is not None:
        source += f":{span_id}"
    return hashlib.sha256(source.encode()).hexdigest()[: 16 if span_id is not None else 32]


def nanos(value):
    if not value:
        return None
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None:
            return None
        delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000
        return result if result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def attribute(key, value):
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    elif isinstance(value, float) and math.isfinite(value):
        encoded = {"doubleValue": value}
    elif isinstance(value, str) and len(value) <= 256:
        encoded = {"stringValue": value}
    else:
        return None
    result = {"key": key, "value": encoded}
    return result


def export_payload(store, run_id):
    view = project_trace(store, run_id)
    spans = []
    omitted = []
    for source in view["spans"]:
        start = nanos(source["start_time"])
        end = nanos(source["end_time"])
        if start is None or end is None or end < start:
            omitted.append(source["span_id"])
            continue
        values = {
            key: value for key, value in source["attributes"].items() if key in SAFE_ATTRIBUTES
        }
        values.update(
            {
                "game_automation.run_id": run_id,
                "game_automation.span_id": source["span_id"],
                "game_automation.task_id": view["trace_id"],
                "gen_ai.span.kind": source["kind"],
                "game_automation.span_status": source["status"],
                "game_automation.measured_duration_available": source["duration_ms"] is not None,
            }
        )
        if source["duration_ms"] is not None:
            values["game_automation.measured_duration_ms"] = source["duration_ms"]
        attributes = [item for key, value in values.items() if (item := attribute(key, value))]
        links = []
        for link in source["links"]:
            target_run = link.get("run_id", run_id)
            target_span = link.get("span_id", target_run)
            item = {
                "traceId": identifier(target_run),
                "spanId": identifier(target_run, target_span),
            }
            links.append(item)
        span = {
            "traceId": identifier(run_id),
            "spanId": identifier(run_id, source["span_id"]),
            "name": source["name"]
            if re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", source["name"])
            else source["kind"],
            "kind": 3 if source["kind"] in ("tool", "chat") else 1,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(end),
            "attributes": attributes,
            "links": links,
            "status": {"code": {"ok": 1, "error": 2}.get(source["status"], 0)},
        }
        if source["parent_span_id"]:
            span["parentSpanId"] = identifier(run_id, source["parent_span_id"])
        spans.append(span)
    resource = [attribute("service.name", "game-automation")]
    version = view["resource"].get("service.version")
    if version:
        resource.append(attribute("service.version", version))
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [item for item in resource if item]},
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "game-automation.trace-export",
                            "version": MAPPING_VERSION,
                        },
                        "spans": spans,
                    }
                ],
            }
        ]
    }
    validate(store.root / "contracts/v1", "otlp-trace-export", payload)
    result = (payload, omitted, len(spans))
    return result


def export_otlp(store, run_id):
    owns_snapshot = not store.db.in_transaction
    if owns_snapshot:
        store.db.execute("BEGIN")
    try:
        payload, omitted, exported = export_payload(store, run_id)
        sequence = store.db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        if owns_snapshot:
            store.db.rollback()
    maximum = settings(store).get("export_max_bytes", 16 * 1024**2)
    application = tomllib.loads(
        (store.root / "config/application.toml").read_text(encoding="utf-8")
    )
    reserve = application.get("storage", {}).get("reserve_bytes", 2 * 1024**3)
    if type(reserve) is not int or reserve < 0:
        raise FrameworkError(ErrorCode.INVALID_STORAGE_POLICY)
    require_disk_space(store.var, reserve + maximum)
    directory = store.run_dir(run_id) / "reports/exports"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "trace.otlp.v1.json.gz"
    descriptor, temporary = tempfile.mkstemp(prefix=".otel-", suffix=".tmp", dir=directory)
    encoded_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
                buffered = bytearray()
                for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(
                    payload
                ):
                    encoded = chunk.encode("utf-8")
                    encoded_bytes += len(encoded)
                    if encoded_bytes > maximum:
                        raise FrameworkError(ErrorCode.TRACE_EXPORT_LIMIT)
                    buffered.extend(encoded)
                    if len(buffered) >= 65536:
                        compressed.write(buffered)
                        buffered.clear()
                if buffered:
                    compressed.write(buffered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    receipt = {
        "run_id": run_id,
        "format": "otlp-json-gzip",
        "mapping_version": MAPPING_VERSION,
        "privacy_policy": "metadata-allowlist-v1",
        "source_event_seq": sequence,
        "exported_spans": exported,
        "omitted_span_count": len(omitted),
        "omitted_spans": omitted[:1000],
        "omission_basis": "Missing or invalid recorded wall-clock boundaries; no invented endings",
        "file": target.relative_to(store.run_dir(run_id)).as_posix(),
        "uncompressed_bytes": encoded_bytes,
        "compressed_bytes": target.stat().st_size,
    }
    atomic_json(directory / "receipt.json", receipt)
    return receipt
