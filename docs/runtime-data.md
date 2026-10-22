# Local runtime data

`var/` is ignored by Git and contains both persistent task data and disposable development material.
Use explicit subdirectories; do not place console exports, helper scripts or stage-specific log
files beside the database.

```text
var/
  state.sqlite                  Authoritative tasks, state, events, progress and device bindings
  state.sqlite-wal              SQLite-managed sidecar when present
  state.sqlite-shm              SQLite-managed sidecar when present
  execution.lock                OS-owned workflow and maintenance coordination
  layout.lock                   OS-owned directory/metadata coordination
  tasks/<task_id>/               Task artifacts and reports
  secrets/                      Current-user encrypted credentials
  inputs/                       Caller-owned APK lists and other private inputs
  logs/
    development.log             One append-only development/maintenance command transcript
    legacy/                     Archived historical command logs
  cache/bytecode/               Rebuildable Python compilation cache
  tools/                        Locally downloaded helper executables
  tmp/
    exports/                    Ad hoc CLI output copies
    scripts/                    Historical development helpers
    codex-protocol/              Generated protocol reference material when retained
    mumu-discovery/              Historical manual discovery material when retained
  migrations/                   Layout backup and relocation records
  cleanup/pending.json          Fixed deletion intent, only during incomplete task cleanup
```

## Logging and evidence

The application already has one authoritative event stream in SQLite. Module calls, actions,
decisions and durations are correlated there, and task reports are projections of those records.
They do not need another per-stage text log.

Historical `push-*.log` and verification logs were development command captures, not framework
stage logs. They are retained under `logs/legacy/`. New development command output is appended to
`logs/development.log`; it is never overwritten and does not create a log per feature or phase.
This transcript is not a substitute for authoritative task events or a second business logger.

Temporary JSON exports and development helper scripts belong under `tmp/`; user-supplied inputs
belong under `inputs/`. Neither is a new source of task state. An old helper archived under tmp/
is not a maintained CLI command. Public source and reusable tools belong in the tracked project
only when actively supported.

## Data lifecycle

Do not remove lock files while the framework is running, or manually move a live SQLite database
and its WAL/SHM files. OS locks coordinate active operations; a lock filename is not a log.
Moving task inputs requires supplying their current location with `--input` on recovery; their URL
digests must still match the original submission.

Use [task cleanup](cleanup.md) to remove task folders together with SQLite history. That command
preserves credentials, inputs, tools, caches, migration backups, temporary exports and operational
logs. Rebuildable caches can be discarded when no related process is using them. Temporary exports
and legacy transcripts may contain private task or operational details; keep them local.
