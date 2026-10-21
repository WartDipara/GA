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

The kernel currently authorizes only `device.install` through its installation dispatcher. It
requires an APK-to-instance reservation, persists intent, and commits uncertain status before
acknowledging the plugin's dispatch request. Direct or nested installation calls lack that grant and
are denied. Other mutating capabilities remain unavailable. Future coordinate actions must reference
a fresh observation and session and stay within bounds. Post-action evidence verifies outcomes; an
ADB success response alone does not.

Existing action records remain readable. Device preparation may resume read-only inspection of an
uncertain install; a new mutation remains blocked until reconciled. Reservations span execution
batches so a runtime upgrade cannot bypass an earlier unknown outcome. Only declared retry-safe,
non-mutating capabilities retry; attempts have separate spans. Overall deadlines depend on the run
mode. Long-running preparation has no aggregate deadline; individual commands retain their own
limits.

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

MuMu is the initial emulator provider. The `emulator_mumu` plugin resolves its local tools and
instance endpoints, queries Android readiness, and captures local screenshots on request. The kernel
validates evidence and records discovery in the `emulators` run mode. Provider-specific paths and
management commands stay inside the adapter. Lifecycle management and automated input remain
separate, unavailable capabilities. See [MuMu discovery](emulators.md).

The independent `android_device` plugin requests `emulator.session` through the host, without
importing adapter code. It inspects packages and Android capacity, installs a bound APK, and reads
the focused logical display with capture-time consistency checks. Shared bounded command transport
and PNG parsing live in the SDK. The kernel alone owns reservations, action states, and evidence
validation. SQLite schema version 2 adds the reservation table without discarding earlier state. See
[device preparation](device-preparation.md). Installation workflow acceptance is pending.

APK preprocessing is a separate run mode that accepts URL-list digests and uses apk.plan to estimate
the whole batch's disk budget before starting downloads. The kernel adds work-file overhead, a
margin, and a free-space reserve. Insufficient capacity blocks execution; recovery estimates again.
Admitted items enter a queue for up to five workers. The kernel calls apk.prepare once per item and
stores compact metadata and a batch summary. Each active plugin owns a download thread and an
OS-locked workspace. Thread events are marshalled to the plugin RPC loop; database writes remain on
the kernel event loop. The caller-owned URL list is read at runtime without copying addresses into
traces. Each item owns its directory and temporary files. See [APK preprocessing](preprocessing.md).

The framework implements communication, persistence, Skills, tracing, context inspection, and task
intake with source attribution and clarification recovery. The environment plugin queries ADB,
selects a device, and checks boot completion and installed package paths. Environment results are
diagnostics, not device-control acceptance. Every resume performs fresh checks; no previous result
authorizes launch. See [task intake](task-intake.md) and [environment checks](environment.md).
General Android lifecycle/input control, perception, exploration algorithms, and game coverage
reports remain unregistered future capabilities. Development verification is compilation only and
does not establish runtime or game acceptance.
