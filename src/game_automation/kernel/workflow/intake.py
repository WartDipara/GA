"""Task intake orchestration. Inputs are data; acceptance belongs to the kernel."""

import json
from pathlib import Path

from game_automation.common import FrameworkError, digest, json_text, redact
from game_automation.contracts import schema, validate
from game_automation.errors import ErrorCode
from game_automation.prompting import build_input

MAX_DOCUMENT_BYTES = 200_000


def read_document(path: Path):
    if path.suffix.lower() not in (".txt", ".md"):
        raise FrameworkError(ErrorCode.UNSUPPORTED_DOCUMENT)
    with path.open("rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise FrameworkError(ErrorCode.DOCUMENT_TOO_LARGE)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FrameworkError(ErrorCode.INVALID_ENCODING) from exc
    text = redact(text.replace("\r\n", "\n").replace("\r", "\n"))
    if not text.strip() or "\x00" in text:
        raise FrameworkError(ErrorCode.INVALID_DOCUMENT)
    document_input = {"kind": "document", "filename": path.name, "text": text}
    return document_input


def revision(sources):
    return digest(json_text(sources).encode("utf-8"))


def source_inputs(store, run_id):
    submission = store.run_task(run_id)
    document = submission.get("kind") == "document"
    sources = [
        {
            "source_id": "submission",
            "text": submission["text"] if document else json.dumps(submission, ensure_ascii=False),
        }
    ]
    sources.extend(
        {"source_id": event["event_id"], "text": event["payload"]["text"]}
        for event in store.events(run_id)
        if event["kind"] == "task.supplement"
    )
    source_context = (submission, sources)
    return source_context


def check_result(contracts, result, sources, defaults, canonical):
    validate(contracts, "intake-result", result)
    if result["status"] == "needs_input":
        if result["task"] is not None or not result["questions"] or result["provenance"]:
            raise FrameworkError(
                ErrorCode.INTAKE_OUTPUT_INVALID, "Clarification needs questions and no task"
            )
        return
    if result["task"] is None or result["questions"]:
        raise FrameworkError(ErrorCode.INTAKE_OUTPUT_INVALID)
    task = result["task"]
    validate(contracts, "task", task)
    if canonical:
        if task != canonical:
            raise FrameworkError(
                ErrorCode.INTAKE_OUTPUT_INVALID, "Canonical input cannot be rewritten"
            )
    elif task["platform"] != "android":
        raise FrameworkError(
            ErrorCode.INTAKE_OUTPUT_INVALID, "Document intake currently supports Android"
        )
    if any(not task[field].strip() for field in ("objective", "target")):
        raise FrameworkError(
            ErrorCode.INTAKE_OUTPUT_INVALID, "Objective and target must contain text"
        )
    source_map = {item["source_id"]: item["text"] for item in sources}
    supported = set()
    for evidence in result["provenance"]:
        field, source_id, quote = evidence["field"], evidence["source_id"], evidence["quote"]
        if source_id == "policy":
            if field not in defaults or task[field] != defaults[field]:
                raise FrameworkError(ErrorCode.INTAKE_OUTPUT_INVALID, "Invalid default attribution")
            try:
                matches = json.loads(quote) == defaults[field]
            except ValueError:
                matches = False
            if not matches:
                raise FrameworkError(
                    ErrorCode.INTAKE_OUTPUT_INVALID, "Default quote does not match policy"
                )
        elif source_id not in source_map or not quote.strip() or quote not in source_map[source_id]:
            raise FrameworkError(ErrorCode.INTAKE_OUTPUT_INVALID, "Source quote cannot be verified")
        # The device identifier must occur verbatim in a supplied source.
        if field == "target" and not canonical and quote != task["target"]:
            raise FrameworkError(
                ErrorCode.INTAKE_OUTPUT_INVALID, "Target must match its source quote"
            )
        supported.add(field)
    if supported != set(task):
        raise FrameworkError(
            ErrorCode.INTAKE_OUTPUT_INVALID, "Every task field needs source attribution"
        )
    if not canonical:
        for field, maximum in defaults["limits"].items():
            if task["limits"][field] > maximum:
                raise FrameworkError(
                    ErrorCode.INTAKE_OUTPUT_INVALID, "Requested limits exceed kernel policy"
                )


async def intake(store, run_id, host, manifest):
    contracts = store.run_dir(run_id) / "contracts"
    submission, sources = source_inputs(store, run_id)
    document = submission.get("kind") == "document"
    validate(contracts, "document" if document else "task", submission)
    current = revision(sources)
    events = store.events(run_id)
    accepted = [event for event in events if event["kind"] == "intake.accepted"]
    if accepted:
        if accepted[-1]["payload"]["revision"] != current:
            raise FrameworkError(ErrorCode.INTAKE_NEW_RUN_REQUIRED)
        return accepted[-1]["payload"]["task"]
    previous = [event for event in events if event["kind"] == "intake.needs_input"]
    if previous and previous[-1]["payload"]["revision"] == current:
        raise FrameworkError(ErrorCode.INTAKE_NEEDS_INPUT)

    policy = manifest["application"].get("intake", {})
    defaults = {
        "platform": "android",
        "scope": ["all reachable interactive elements"],
        "limits": {
            "max_actions": manifest["application"]["max_actions"],
            "timeout_seconds": policy.get("timeout_seconds", 300),
        },
        "constraints": [],
    }
    source_context = {"sources": sources, "defaults": defaults}
    context_ref = store.artifact(run_id, f"artifacts/intake/{current}/sources.json", source_context)
    input_payload = {
        "revision": current,
        "context_ref": context_ref,
        "source_ids": [source["source_id"] for source in sources],
    }
    store.event(run_id, "intake.input", input_payload)
    params = {
        "task": None if document else submission,
        "sources": sources,
        "defaults": defaults,
        "ai": None,
    }
    if document:
        workspace = store.run_dir(run_id) / "ai-workspace"
        workspace.mkdir(exist_ok=True)
        params["ai"] = {
            "codex": manifest["application"]["codex"],
            "workspace": str(workspace),
            "role": "intake",
            "thread_id": store.session(run_id, "intake"),
            "turn": build_input(
                store,
                run_id,
                "intake",
                source_context,
                schema(contracts, "intake-result"),
            ),
        }
    validate(contracts, "intake-request", params)
    result = await host.call(
        "task.intake", params, timeout=manifest["application"]["codex"]["timeout_seconds"] + 60
    )
    call = next(
        event
        for event in reversed(store.events(run_id))
        if event["kind"] == "call.started" and event["payload"]["capability"] == "task.intake"
    )
    ids = {key: call[key] for key in ("span_id", "parent_span_id", "request_id")}
    try:
        check_result(contracts, result, sources, defaults, None if document else submission)
    except FrameworkError as exc:
        store.event(run_id, "intake.rejected", {"revision": current, "error": exc.as_dict()}, **ids)
        raise
    result_ref = store.artifact(run_id, f"artifacts/intake/{current}/result.json", result)
    payload = {**result, "revision": current, "context_ref": result_ref}
    if result["status"] == "needs_input":
        store.event(run_id, "intake.needs_input", payload, **ids)
        raise FrameworkError(
            ErrorCode.INTAKE_NEEDS_INPUT, "Task needs clarification; inspect questions in status"
        )
    # The accepted task and its evidence commit in one append-only authoritative event.
    store.event(run_id, "intake.accepted", payload, **ids)
    return result["task"]


def intake_status(store, run_id):
    kinds = ("intake.accepted", "intake.needs_input", "intake.rejected")
    events = store.events(run_id, kinds=kinds, limit=1)
    return events[-1]["payload"] if events else None
