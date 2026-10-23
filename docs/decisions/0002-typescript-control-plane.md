# ADR 0002: Typed control plane with Python capability plugins

Status: accepted for incremental implementation. Supersedes ADR 0001's Python-only language choice.

Use TypeScript for workflow scheduling, gameplay control, process supervision, provider adapters,
trace ingestion and authoritative state transitions. Retain Python 3.12 for APK analysis, image/OCR
processing, Windows credential protection and existing device capabilities. JSON-RPC 2.0 and
contracts/v1 remain the cross-language boundaries. Do not wrap the complete Python kernel and call
that a migrated TypeScript control plane.

Strict type checking, checked indexed access, exact optional fields, unknown catch values and
exhaustive state handling are required. External process and model output begins as unknown. Runtime
JSON Schema validation remains mandatory; static types cannot establish device success, durable
writes, model quality or freedom from runtime exceptions. Expected failures are structured domain
results; unexpected failures retain their cause and associated trace before execution stops.

Generate types from the shared schemas. During migration, generate the TypeScript ErrorCode from
the existing Python declaration without importing or executing application code. This preserves one
error-code authority until its ownership is explicitly migrated. Pin npm dependencies and Node.
Generated code and bundles are shared build artifacts under var/cache/typescript, never task copies.

Migration order:

1. Strict build, shared contracts, errors and process communication.
2. Replace the Codex adapter, then the plugin host and trace ingestion/storage.
3. Replace workflow scheduling, action authorization, recovery and progress state.
4. Replace gameplay planning/routing/routines and retire their Python implementations.
5. Move CLI and observability serving where required; retain pure Python capability implementations.

Each increment has its own feature branch and publication. A module is registered only when its
implementation replaces a real path. There must be exactly one authoritative state/event writer.
SQLite schemas, task ownership, event IDs, sequence order, session IDs, action uncertainty and
report-only recovery must survive language changes. Never migrate stored evidence by replaying
device actions. Existing runs retain their original dependencies and require a new batch after a
runtime change. Old evidence must remain readable.

Verification remains compilation/type checking and source syntax checks. No mock, smoke or synthetic
inference calls are permitted. Runtime acceptance and latency improvement require a separately
authorized, bounded business execution.

Implemented increments:

- Strict generated contracts, shared errors and TypeScript build/type-check gates.
- TypeScript JSON-RPC/process support and the active Codex adapter, replacing its Python entry.
- TypeScript plugin supervision, trace ingestion and the sole SQLite writer. Task creation, state
  settlement, report requests, sessions, action authorization/uncertainty, checkpoints and cleanup
  transactions now execute in this owner. Python database connections are read-only.

Execution lifecycle, interrupted-span recovery and the APK scheduler now run in TypeScript.
Python CLI compatibility functions hold the existing OS execution lock and invoke that executor;
they do not select stages, reuse child executions or publish business terminal states. During this
increment a named reverse-RPC bridge retains existing leaf-stage operations, snapshot utilities and
rich report renderers. This bridge is transitional and must be removed as those operations become
TypeScript orchestration plus independent Python tools. The Python gameplay loop is still pending.

The executor preserves stage keys, child ownership, continued-from links, explicit unknown spans,
actual duration fields, atomic settlement/report requests and report-only recovery. A broken bridge
can produce a minimal degraded outcome report without replaying business operations.

Use the pinned Node built-in SQLite implementation behind a single database module, preserving the
existing schema, WAL/FULL durability and event-byte triggers. Its API is experimental in this Node
release; pinning and compilation reduce compatibility risk but do not prove runtime correctness.
Windows named-pipe exclusivity establishes writer ownership before opening the database. Private
authenticated loopback RPC lets cancellation and queries coexist with long plugin calls without
creating another writer. This adds no public HTTP task API and no independent business log system.
