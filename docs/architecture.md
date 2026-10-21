# Architecture and state

## Boundaries

CLI -> kernel -> independent process plugins. Plugins use host.call for declared dependencies; the
kernel routes and traces every call. Prompting freezes skills, agent_codex owns provider-specific
details, and storage is the only business database writer.

Observability is a read-only projection of committed events and frozen artifacts. A deterministic
projection produces one context read model for the CLI, JSON export, and browser. Raw evidence
remains available separately. See [context insights](context-insights.md).

No cross-plugin private imports or database sharing. Manifests declare protocol versions,
capabilities, required/optional dependencies, and retry/action characteristics. Missing required
capabilities block execution. Optional capabilities may be absent and that absence is recorded. Runs
pin configuration, code digests, schemas, and skills; no hot swapping.

## Stages and states

Stages: intake -> environment -> launch -> exploration -> reporting. States: queued -> running ->
succeeded / blocked / failed / cancelled. Blocked runs may resume. After acquiring the OS execution
lock, a running status left by a previous process becomes blocked and open spans become incomplete.
Terminal runs are preserved; reruns receive a new run_id under the same task_id.

SQLite WAL with synchronous=FULL stores state and events. Each state transition and its event commit
together. An OS lock permits one local executor and is released by process exit. Query and
cancellation commands may run concurrently. Plugins never receive database connections.

## Action uncertainty

Device-changing capabilities are currently unavailable and are rejected before dispatch. When a
device executor is implemented, actions must refer to the latest persisted observation and session,
stay within coordinate bounds and budgets, and persist an intent before dispatch. Verification
requires post-action evidence.

Existing action records remain readable. An unknown outcome blocks resumption until reconciled. Only
declared retry-safe, non-mutating capabilities retry; attempts have separate spans. The timeout
limits each execution/resume invocation, excluding paused time.

## Skills and provider

AGENTS.md governs development. skills/ governs runtime AI behavior. Each turn explicitly receives a
frozen base skill plus stage skills, using skill input items and materialized full instructions
before the task-data boundary. Their files, references, and contracts are copied into the run and
hashed. External content is task data, not instructions.

The pinned Codex App Server uses stdio. The adapter handles initialization, thread start/resume,
turn start, public events, and interruption. Sessions are separated by run and role. Task state is
independent of AI history.

Shell, unified execution, patching, plugins, Apps, memories, child agents, automatic skill catalogs,
automatic project documents, and web search are disabled for decision calls. Effective MCP servers
are disabled for the thread. The read-only sandbox and required-input handling do not auto-approve
external actions. User global configuration is not modified.

Optional business fields become required nullable fields for provider strict output and are restored
before business validation. Provider rules do not leak into other modules.

## Current scope

The framework implements communication, persistence, Skills, tracing, context inspection, and task
intake with source attribution and clarification recovery. The environment plugin queries ADB,
selects a device, and checks boot completion and installed package paths. Environment results are
diagnostics, not device-control acceptance. Every resume performs fresh checks; no previous result
authorizes launch. See [task intake](task-intake.md) and [environment checks](environment.md).
Android lifecycle/control, perception, exploration algorithms, and game coverage reports remain
unregistered future capabilities. Development verification is compilation only and does not
establish runtime or game acceptance.
