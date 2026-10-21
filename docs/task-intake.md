# M1 task intake

## Boundaries

The kernel accepts immutable submissions, constructs prompts, enforces limits, validates results,
and persists acceptance. The independent task_intake plugin accepts canonical tasks without
inference or calls its optional ai.decide dependency for documents. It does not import the Codex
plugin, read SQLite, or operate a device. Removing the AI provider still permits canonical intake;
documents block on the missing dependency.

The first document adapter reads UTF-8 TXT and Markdown locally, with optional BOM and normalized LF
endings. It checks extension, a 200,000-byte limit, decoding, empty content, and null bytes before
submission. It does not follow links, execute embedded commands, extract attachments, parse
Word/PDF, or perform OCR. Existing redaction is applied before storage; pattern redaction cannot
guarantee arbitrary prose contains no secrets. Do not submit credentials.

## Submission and acceptance

The tasks.spec column holds either a canonical task or a document envelope. The submission remains
the user's original redacted input; it is never overwritten by AI output. A successful intake
records the separately validated canonical task in intake.accepted. Downstream phases receive this
accepted value. Existing offline reports remain readable; code changes require a new execution
batch.

Sources have stable IDs: submission and the event_id of each task.supplement. A SHA-256 digest of
their ordered content identifies the input revision. Each request snapshots sources/defaults into
artifacts/intake/<revision>/sources.json and each result into result.json. intake.input,
intake.needs_input, intake.rejected, and intake.accepted retain the revision. Result events share
the task.intake span, whose ai.decide child records actual prompt inputs, frozen skills, public
provider events, and structured output.

All fields in an accepted task need source attribution. Quotes must occur in supplied source text,
and the Android target must equal its quote. Defaults are explicitly attributed to policy, with a
JSON quote matching the default value. The kernel does not infer source validity from an AI
explanation. Quotes establish a traceable source, not that a paraphrase perfectly preserves all
semantics. Real extraction quality still requires representative document evaluation.

Default platform is Android; scope is all reachable interactive elements; constraints are empty
unless specified. For document extraction, max_actions comes from application policy;
timeout_seconds comes from the intake configuration. Extracted limits may be smaller and may not
exceed those caps. Canonical JSON preserves explicitly submitted limits without rewriting; action
execution still applies the kernel's configured cap. Objective and target have no defaults.
Unsupported platforms, missing fields, and conflicts should produce questions. Semantic validation
failures fail the run and preserve the rejected output; they are not silently repaired or retried.

## Clarification and recovery

needs_input returns task:null, nonempty questions, no provenance, and a concise reason_summary. No
partial task is treated as accepted. provide appends a supplement while blocked; resume reads the
original submission plus all supplements. The same revision reuses the outstanding questions without
another AI call. Roles isolate Codex sessions by run and intake; a completed intake never shares
exploration context.

Acceptance is one authoritative append-only event, including the task and provenance. If a process
stops before acceptance, a later attempt may call AI again, but no device action can have occurred
in this stage. Once acceptance commits, a resumed game run reuses it without duplicate intake
acceptance or model calls. The CLI rejects requirement changes after acceptance; create a new task
instead. Unknown device actions are still blocked by the workflow's existing reconciliation rule.

An intake-only run succeeds at stage intake. A game run proceeds to environment and currently blocks
on environment.check, which is deliberately unregistered. It cannot report whole-game success.
Cancellation, process failure, timeout, unavailable AI, invalid output, and trace persistence
failure retain the existing host guarantees. Intake has a configured wall-clock budget per
execution/resume invocation; paused time is excluded.

## Protocol

intake-request.schema.json describes the plugin request: nullable canonical task, source list,
policy defaults, and nullable AI request assembled by the kernel. intake-result.schema.json
describes status, nullable canonical task, questions, reason_summary, and provenance. The inline
canonical shape follows task.schema.json. Cross-field rules and quote/budget checks are enforced
after the schema at the kernel boundary.

No UI or hidden chain-of-thought access is required. The existing offline timeline shows the source
snapshots, module call tree, clarification, acceptance or rejection, and actual public AI events.
Inspect with status, trace, inspect, context, or export-trace. Offline browsing does not parse a
document again or call a model/device.

## Skill delivery compatibility

Prompt assembly includes explicit Skill items and the full frozen instructions in submitted text.
Instruction delivery remains deterministic when automatic provider discovery is disabled, and the
recorded request shows exactly what the caller supplied. The provider's hidden system prompt is not
exposed. See the [App Server Skill protocol](https://learn.chatgpt.com/docs/app-server#skills).
