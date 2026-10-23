# Plugin protocol v1.0

`agent-trace.schema.json` defines the read-only application span projection. It retains native
span/request IDs, actual timing and usage evidence, retry/execution links and explicit unknowns.
`trace-analysis.schema.json` defines evidence-cited analysis proposals, not executable actions.
Neither format replaces the authoritative event envelope or the plugin transport. See
[Agent observability](agent-observability.md) for replay, metrics and label semantics.

Transport: UTF-8 JSON-RPC 2.0 over stdio, one JSON object per line, maximum 8 MiB per message.
stdout is protocol-only; stderr carries diagnostics. Use artifact references for large payloads.

`workflow.schema.json` defines kernel workflow submissions: ordered APK source hashes, dedicated
instance assignments, stage composition and gameplay goal. The original APK URL file remains a
private CLI input. Child executions carry kernel-owned `workflow_run` and `workflow_key` metadata
in their stored inputs; these fields are not added to plugin business request payloads.

## Manifest

plugin.toml declares name, version, protocol_version, command, capabilities, required_capabilities,
optional_capabilities, retry_safe, and mutating. contracts/v1/plugin.schema.json is authoritative.
command is an argument array without shell interpolation; {python} and {plugin} placeholders are
supported. A separate interpreter may point to a plugin's own virtual environment. Only one provider
may register a capability.

## Lifecycle

1. Persist call.started and its input before starting the process.
2. initialize({protocol_version:"1.0"}) returns protocol_version/version/capabilities matching the
   manifest.
3. health({}) returns {ok:true}.
4. invoke({capability,input,context}) returns a business result. context carries
   task_id/run_id/span_id/parent_span_id/request_id.
5. Persist the result or error and terminate the owned process tree. The current host uses one
   plugin process per attempt.

Reverse requests:

- `host.event({kind,payload,event_id?})`: `provider.*`, `ai.*`, and `plugin.*` namespaces; await
  `persisted:true` before continuing.
- host.call({capability,input}): declared dependencies only. The kernel creates a child span and
  rejects dependency cycles.
- `host.action({instance_id,serial})`: only the current kernel-dispatched `device.install` call may
  request this grant. The kernel commits the action as unknown before returning `persisted:true`.
  Acknowledgement is required immediately before the installation command; it does not verify an
  outcome or grant any other device capability.

Notifications do not prove business success. Results and post-action observations establish
outcomes.

## Errors and events

ErrorCode centralizes framework wire codes and default messages. FrameworkError accepts an enum
member and an optional contextual message. The wire values remain strings; unknown remote codes are
preserved. Provider error classification stays in its adapter.

error.data contains code/message/retryable. The host enforces deadlines and records abnormal exits,
invalid stdout, timeouts, and cancellations. Errors do not echo invalid field values. Only
explicitly retry-safe non-mutating calls retry.

Raw protocol messages and normalized events are retained. Stable event_id values deduplicate
records; conflicting contents are rejected. Text deltas are not deduplicated by guessing that
identical text is redundant. The kernel seq is receive order, not cross-process wall-clock order.

trace.schema defines the public envelope. Payloads depend on event kind. Provider-specific payloads
remain diagnostic records. Observations, decisions, actions, and results use their own schemas.
Codex omits jsonrpc on its wire; only the adapter knows that difference.

## Development verification

Compile maintained Python sources and syntax-check maintained JavaScript. Do not add or run mock or
smoke tests. Business schemas, permission checks, trace persistence, and recovery rules remain
enforced during actual task execution; they are not replaced by compilation checks.

## APK preprocessing

The read-only `apk.preflight` capability uses apk-preflight-request and apk-preflight-result. It
checks AAPT2 before source probes or download dispatch and returns its resolved executable. The
kernel records `preprocessing.tools.checked` and supplies that executable to item workers.

The `apk.plan` capability uses apk-plan-request and returns apk-download-plan. Requests reference
one source entry and workspace. Results contain `budget_bytes`, `size_bytes` (null when unknown),
and `basis`: local_apk, content_length, content_range, or size_limit. No APK body is saved by this
capability. The kernel validates all plans, estimates the complete batch, and persists
`preprocessing.storage.checked` before allowing download dispatch. `local_apk` covers a completed
APK or a complete checkpoint awaiting local hash and ZIP validation; its zero budget permits
verification only, never an automatic replacement download. Planning may persist a retry cooldown in
the assigned workspace under its lock; it never saves an APK body. An active cooldown suppresses
further source probes until it expires.

The CLI accepts apk-batch JSON. The apk.prepare capability uses apk-prepare-request and returns
apk-metadata. Requests reference a caller-owned list file, item index, source digest, assigned
workspace, and download settings. The input file is read only, and its selected URL must match the
digest. Raw addresses are not included in RPC messages. The kernel validates requests and metadata;
the plugin writes only its assigned APK files and download checkpoint, never SQLite.

The kernel supplies the additive `download_budget_bytes`, `disk_reserve_bytes`, `timeout_seconds`,
and `retry_max_seconds` request fields. A zero `timeout_seconds` disables the overall item deadline;
network I/O and plugin handshakes retain separate finite timeouts. A zero `max_attempts` allows
automatic transient retries without an attempt cap. Positive values preserve explicit bounds.
Workers enforce their own admitted limit without sharing mutable download state. Storage failures
block the whole batch, stop new dispatch, and cancel active workers. Only a fresh admission on
resume can replace a budget; no download retry raises it automatically.

preprocessing.apk.queued/started/ready/blocked events identify each item. The kernel reserves a
distinct span when an item enters the FIFO queue, and the host uses that span for its capability
call. Progress, errors, and completion stay associated with the item even when five workers
interleave. plugin.preprocessing events expose HTTP attempts, progress, restarts, archive
validation, and metadata-tool output. The kernel writes metadata and batch-summary artifacts. Resume
reuses verified local files or continues safe partial downloads, with distinct call spans. See
[APK preprocessing](preprocessing.md).

`plugin.preprocessing.http.redirect` records each explicit redirect without its body or URL.
`plugin.preprocessing.download.retry_scheduled` records waits; `retry_deferred` records a wait that
cannot fit an explicitly configured deadline. `retry_waiting` records an active wait restored from
the checkpoint when necessary. Earlier attempts are retained. Kernel capability calls accept
`timeout=0` for long-running invocations without disabling initialization or health deadlines.

## Emulator discovery

`emulator.discover` uses emulator-discovery-request and emulator-discovery-result. The kernel passes
optional local tool overrides, an optional instance index, capture permission, an artifact
directory, and a per-command timeout. ADB endpoints come from MuMu's manager rather than a default
port.

Results identify each instance as stopped, starting, ready, or blocked. Ready entries include
serial, Android version, ABIs, dimensions, and current shell UID. Optional screenshots carry a safe
basename, bounded byte count, dimensions, and SHA-256. The kernel validates the actual files and
adds evidence references in `emulators.discovered`. Raw capability results remain unchanged in their
call records. Missing tools and ambiguous installations block execution; device-level failures
remain attached to their own instances. No APK or emulator lifecycle changes are performed.

`plugin.mumu.command.started/completed/incomplete` records bounded command evidence. Screenshot
commands record image metadata without embedding PNG bytes into the RPC stream. The plugin never
accesses SQLite. See [MuMu discovery](emulators.md).

## Bound Android preparation

`emulator.session` uses emulator-session-request and emulator-session-result. An explicit instance
index is required; nullable identity and serial allow initial read-only resolution. Non-null values
must match the current manager record. The response identifies the instance and resolved local ADB
executable. Session resolution has no aggregate deadline; each child command remains bounded.

`device.inspect`, `device.install`, and `device.observe` use their corresponding request/result
schemas. Each request includes a pinned session request, resolved again through the declared
emulator.session dependency. Android operations bind an explicit serial. Inspection returns package
presence, installed base APK fingerprint, and available Android data bytes. Installation
additionally requires source path, size, fingerprint, admitted space, and a finite installation
deadline. An install response distinguishes installed from explicitly rejected; transport
uncertainty raises an error rather than returning a synthetic failure result. The host never retries
mutations.

The kernel records action.intent, action.dispatched, and action.result under the installation span.
Only fresh package evidence can move an executed or unknown action to verified. A new batch for the
same APK workspace consults the reservation's earlier action history; reconciliation references its
original run. Device preparation emits device.storage.checked, device.package.observed,
device.installation.reconciled when applicable, and device.prepared. Raw failed attempts remain.

Observations identify the focused logical display, foreground component, rotation, dimensions, and
bounded PNG metadata. The kernel checks the actual file and adds a local evidence reference and
observation ID to device.observed. `plugin.android.command.*` captures adapter command evidence;
`plugin.mumu.command.*` retains provider-specific discovery and session records. No plugin writes
business tables. See [device preparation](device-preparation.md) for recovery and capture limits.

## Task intake

The task.intake capability uses intake-request and intake-result schemas. Canonical input is
returned unchanged; document extraction uses the declared optional ai.decide dependency. The kernel
assembles explicit frozen intake skills and outputSchema before the plugin call. Host schema checks
happen before call.completed; semantic source, status, and budget validation happens before
intake.accepted. An accepted task is distinct from the original immutable submission. See
[task intake](task-intake.md) for defaults, questions, revision IDs, and recovery semantics.

## Environment checks

The environment.check capability uses environment-request and environment-result schemas. The
request supplies the platform, target package, executable, optional serial, and per-command timeout.
The kernel validates the request before dispatch and the host validates the result before
call.completed. A ready result requires an identified device, installed package paths, and passed
checks. A blocked result includes at least one check with a diagnostic code and message.

Each ADB query receives a command_id. The plugin awaits persistence of
plugin.environment.command.started before starting its child process, then records command.completed
or command.incomplete with the same identifier. Checks reference those command identifiers. The
kernel records environment.checked under the capability's span. status includes the latest completed
check result; trace retains all attempts, including incomplete calls.

The capability is non-mutating and does not retry automatically. These checks do not prove launch,
permission readiness, observation availability, or game coverage. See
[environment checks](environment.md).
