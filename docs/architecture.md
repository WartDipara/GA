# Architecture and state

The control plane uses TypeScript for the Codex adapter, plugin host and authoritative SQLite
writer, execution lifecycle, APK scheduler and gameplay loop. Migration is incremental; Python retains
capability tools, CLI entry points, remaining leaf stages and read-only report projections. See
[ADR 0002](decisions/0002-typescript-control-plane.md) for ownership and compatibility requirements.

The transitional executor bridge invokes named Python leaf operations, snapshot utilities and
renderers; it does not delegate scheduling, gameplay decisions, publication order or terminal-state policy. Python entry points
hold the existing OS execution lock across the remote request. Connection loss aborts execution,
drains plugin calls, and leaves unknown device outcomes for observation-based recovery.

## Boundaries

CLI -> kernel -> independent process plugins. Plugins use host.call for declared dependencies; the
kernel routes and traces every call. Prompting pins shared resources, agent_codex owns provider-specific
details, and storage is the only business database writer.

Observability is a read-only projection of committed events and frozen artifacts. A deterministic
projection produces one context read model for the CLI, JSON export, and browser. Raw evidence
remains available separately. See [context insights](context-insights.md).
`observability.py` owns that projection. `reporting/` owns offline rendering and evidence summaries;
its `web/` directory contains the shared HTML template, CSS and JavaScript. The kernel and storage
remain responsible for authoritative event writes. See [source layout](project-layout.md).

No cross-plugin private imports or database sharing. Manifests declare protocol versions,
capabilities, required/optional dependencies, and retry/action characteristics. Missing required
capabilities block execution. Optional capabilities may be absent and that absence is recorded. Runs
pin configuration, code digests, schemas, and skills; no hot swapping.

## Stages and states

Stages: intake -> environment -> launch -> exploration -> reporting. States: queued -> running ->
succeeded / blocked / failed / cancelled. Blocked runs may resume. After acquiring the OS execution
lock, a running status left by a previous process becomes blocked and open spans become incomplete.
Terminal runs are preserved; reruns receive a new run_id under the same task_id.
The task is the business submission, not an individual stage. Each run has an immutable execution
input separate from the original task submission. Preprocessing, preparation and gameplay share
task ownership through explicit source-run references. Their directories live under
`var/tasks/<task_id>/executions/<run_id>/`. See [task workspaces](task-workspaces.md).

SQLite WAL with synchronous=FULL stores state and events. Each state transition and its event commit
together. An OS lock permits one local executor and is released by process exit. Query and
cancellation commands may run concurrently. Plugins never receive database connections.

The Windows control-plane process acquires an exclusive named-pipe lease before opening SQLite.
All CLI processes share this owner through authenticated loopback JSON-RPC. Endpoint metadata stays
in the ignored shared cache; this is a private kernel interface, not an HTTP submission service.
The lease is released only after database close or process exit. Python connections use `mode=ro`
and `query_only`; mutations, event batches, sessions, reservations, action states, checkpoints and
cleanup commits are named kernel commands. No arbitrary SQL write endpoint or Python write fallback
exists. Synchronous transactions cannot interleave across asynchronous plugin calls.

Before recovering abandoned execution spans, the next execution-lock owner cancels and drains
previous plugin calls. Connection loss aborts owned calls; unknown dispatched actions remain
unknown. A durable request receipt rejects duplicate host request IDs even after owner restart.
Long host requests check owner liveness without resending their original request. Idle owners close
after the last client and call depart. A different bundle fingerprint requires the old owner to
finish before a new build can connect.

The Node SQLite API is isolated in `control_plane/storage/database.ts` and uses the pinned Node
runtime. This API is experimental in the selected Node release; dependency upgrades require explicit
review. Compilation does not establish runtime acceptance of the migrated path.

## Action uncertainty

The kernel authorizes `device.install` and `device.execute` through separate dispatchers. Each
requires an APK-to-instance reservation, persists intent, and commits uncertain status before
acknowledging the plugin's dispatch request. Direct or nested installation calls lack that grant and
are denied. MuMu activation inherits only a matching authorized launch/return action. Coordinate
actions reference fresh observations and stay within bounds. Post-action evidence verifies outcomes; an
ADB success response alone does not.

Existing action records remain readable. Device preparation may resume read-only inspection of an
uncertain install; a new mutation remains blocked until reconciled. Reservations span execution
batches so a runtime upgrade cannot bypass an earlier unknown outcome. Only declared retry-safe,
non-mutating capabilities retry; attempts have separate spans. Overall deadlines depend on the run
mode. Long-running preparation has no aggregate deadline; individual commands retain their own
limits.

## Skills and provider

AGENTS.md governs development. skills/ governs runtime AI behavior. Each turn explicitly receives a
base Skill plus stage Skills from shared project resources, using explicit Skill paths and
materialized full instructions before the task-data boundary. Execution manifests pin resource roots
and content hashes without copying Skills or contracts. Resume requires the same dependency version;
missing or changed resources block execution. Submitted requests remain trace evidence. External
content is task data, not instructions.

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
management commands stay inside the adapter. Guarded application activation is available through
the kernel; emulator restart/creation remain outside this release. See [MuMu discovery](emulators.md).

The independent `android_device` plugin requests `emulator.session` through the host, without
importing adapter code. It inspects packages and Android capacity, installs a bound APK, and reads
the focused logical display with capture-time consistency checks. Shared bounded command transport
and PNG parsing live in the SDK. The kernel alone owns reservations, action states, and evidence
validation. SQLite schema version 2 added the reservation table without discarding earlier state. See
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
The gameplay runtime now exposes guarded Android actions, process/task observation and scoped
logcat. SQLite schema version 3 adds shared gameplay checkpoints. The kernel persists intents before
granting dispatch; the MuMu activation dependency inherits only the matching launch/return grant.
An already installed matching APK can be explicitly adopted without reinstalling. Local credentials
are protected by current-user Windows DPAPI and referenced by profile. See [gameplay](gameplay.md).
Local perception, routines, planning, scoped progress and native battle goals are independent
components. Coverage remains limited to actual evidence. Compilation does not establish runtime or
game acceptance.
