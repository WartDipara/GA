"""Task intake orchestration. Inputs are data; acceptance belongs to the kernel."""

import json
from pathlib import Path

from game_automation.common import FrameworkError, digest, json_text, redact
from game_automation.contracts import schema, validate
from game_automation.prompting import build_input

MAX_DOCUMENT_BYTES = 200_000


def read_document(path: Path):
    if path.suffix.lower() not in (".txt", ".md"):
        raise FrameworkError("UNSUPPORTED_DOCUMENT", "Supported documents: UTF-8 .txt and .md")
    with path.open("rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise FrameworkError("DOCUMENT_TOO_LARGE", "Document exceeds the 200,000-byte limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FrameworkError("INVALID_ENCODING", "Document must be UTF-8") from exc
    text = redact(text.replace("\r\n", "\n").replace("\r", "\n"))
    if not text.strip() or "\x00" in text:
        raise FrameworkError("INVALID_DOCUMENT", "Document is empty or contains null bytes")
    return {"kind": "document", "filename": path.name, "text": text}


def revision(sources):
    return digest(json_text(sources).encode("utf-8"))


def source_inputs(store, run_id):
    submission = store.task(store.run(run_id)["task_id"])
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
    return submission, sources


def check_result(contracts, result, sources, defaults, canonical):
    validate(contracts, "intake-result", result)
    if result["status"] == "needs_input":
        if result["task"] is not None or not result["questions"] or result["provenance"]:
            raise FrameworkError(
                "INTAKE_OUTPUT_INVALID", "Clarification needs questions and no task"
            )
        return
    if result["task"] is None or result["questions"]:
        raise FrameworkError(
            "INTAKE_OUTPUT_INVALID", "A ready result needs a task and no questions"
        )
    task = result["task"]
    validate(contracts, "task", task)
    if canonical:
        if task != canonical:
            raise FrameworkError("INTAKE_OUTPUT_INVALID", "Canonical input cannot be rewritten")
    elif task["platform"] != "android":
        raise FrameworkError("INTAKE_OUTPUT_INVALID", "Document intake currently supports Android")
    if any(not task[field].strip() for field in ("objective", "target")):
        raise FrameworkError("INTAKE_OUTPUT_INVALID", "Objective and target must contain text")
    source_map = {item["source_id"]: item["text"] for item in sources}
    supported = set()
    for evidence in result["provenance"]:
        field, source_id, quote = evidence["field"], evidence["source_id"], evidence["quote"]
        if source_id == "policy":
            if field not in defaults or task[field] != defaults[field]:
                raise FrameworkError("INTAKE_OUTPUT_INVALID", "Invalid default attribution")
            try:
                matches = json.loads(quote) == defaults[field]
            except ValueError:
                matches = False
            if not matches:
                raise FrameworkError("INTAKE_OUTPUT_INVALID", "Default quote does not match policy")
        elif source_id not in source_map or not quote.strip() or quote not in source_map[source_id]:
            raise FrameworkError("INTAKE_OUTPUT_INVALID", "Source quote cannot be verified")
        # The device identifier must occur verbatim in a supplied source.
        if field == "target" and not canonical and quote != task["target"]:
            raise FrameworkError("INTAKE_OUTPUT_INVALID", "Target must match its source quote")
        supported.add(field)
    if supported != set(task):
        raise FrameworkError("INTAKE_OUTPUT_INVALID", "Every task field needs source attribution")
    if not canonical:
        for field, maximum in defaults["limits"].items():
            if task["limits"][field] > maximum:
                raise FrameworkError(
                    "INTAKE_OUTPUT_INVALID", "Requested limits exceed kernel policy"
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
            raise FrameworkError(
                "INTAKE_NEW_RUN_REQUIRED", "Accepted input changed; create a new run"
            )
        return accepted[-1]["payload"]["task"]
    previous = [event for event in events if event["kind"] == "intake.needs_input"]
    if previous and previous[-1]["payload"]["revision"] == current:
        raise FrameworkError(
            "INTAKE_NEEDS_INPUT", "Provide the requested information before resume"
        )

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
    context_ref = store.artifact(
        run_id,
        f"artifacts/intake/{current}/sources.json",
        {"sources": sources, "defaults": defaults},
    )
    store.event(
        run_id,
        "intake.input",
        {
            "revision": current,
            "context_ref": context_ref,
            "source_ids": [s["source_id"] for s in sources],
        },
    )
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
                {"sources": sources, "defaults": defaults},
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
            "INTAKE_NEEDS_INPUT", "Task needs clarification; inspect questions in status"
        )
    # The accepted task and its evidence commit in one append-only authoritative event.
    store.event(run_id, "intake.accepted", payload, **ids)
    return result["task"]


def intake_status(store, run_id):
    events = [
        event
        for event in store.events(run_id)
        if event["kind"] in ("intake.accepted", "intake.needs_input", "intake.rejected")
    ]
    return events[-1]["payload"] if events else None
