# Plugin protocol v1.0

Transport: UTF-8 JSON-RPC 2.0 over stdio, one JSON object per line, maximum 8 MiB per message. stdout is protocol-only; stderr carries diagnostics. Use artifact references for large payloads.

## Manifest

plugin.toml declares name, version, protocol_version, command, capabilities, required_capabilities, optional_capabilities, retry_safe, and mutating. contracts/v1/plugin.schema.json is authoritative. command is an argument array without shell interpolation; {python} and {plugin} placeholders are supported. A separate interpreter may point to a plugin's own virtual environment. Only one provider may register a capability.

## Lifecycle

1. Persist call.started and its input before starting the process.
2. initialize({protocol_version:"1.0"}) returns protocol_version/version/capabilities matching the manifest.
3. health({}) returns {ok:true}.
4. invoke({capability,input,context}) returns a business result. context carries task_id/run_id/span_id/parent_span_id/request_id.
5. Persist the result or error and terminate the owned process tree. The current host uses one plugin process per attempt.

Reverse requests:

- host.event({kind,payload,event_id?}): provider.*, ai.*, and plugin.* namespaces; await persisted:true before continuing.
- host.call({capability,input}): declared dependencies only. The kernel creates a child span and rejects dependency cycles.

Notifications do not prove business success. Results and post-action observations establish outcomes.

## Errors and events

error.data contains code/message/retryable. The host enforces deadlines and records abnormal exits, invalid stdout, timeouts, and cancellations. Errors do not echo invalid field values. Only explicitly retry-safe non-mutating calls retry.

Raw protocol messages and normalized events are retained. Stable event_id values deduplicate records; conflicting contents are rejected. Text deltas are not deduplicated by guessing that identical text is redundant. The kernel seq is receive order, not cross-process wall-clock order.

trace.schema defines the public envelope. Payloads depend on event kind. Provider-specific payloads remain diagnostic records. Observations, decisions, actions, and results use their own schemas. Codex omits jsonrpc on its wire; only the adapter knows that difference.

## Development verification

Compile maintained Python sources and syntax-check maintained JavaScript. Do not add or run mock or smoke tests. Business schemas, permission checks, trace persistence, and recovery rules remain enforced during actual task execution; they are not replaced by compilation checks.

## Task intake

The task.intake capability uses intake-request and intake-result schemas. Canonical input is returned unchanged; document extraction uses the declared optional ai.decide dependency. The kernel assembles explicit frozen intake skills and outputSchema before the plugin call. Host schema checks happen before call.completed; semantic source, status, and budget validation happens before intake.accepted. An accepted task is distinct from the original immutable submission. See [task intake](task-intake.md) for defaults, questions, revision IDs, and recovery semantics.
