# Task workspaces

A task is one business submission. An execution is a preserved attempt at one stage or goal.
The original submission stays immutable; execution-specific inputs are stored separately in
SQLite. Module boundaries and plugin protocols remain unchanged.

```text
var/tasks/<task_id>/
  task.json
  apks/index.json
  executions/<run_id>/
    manifest.json
    calls/
    artifacts/
    apks/                 # Present for preprocessing; isolated workspace per APK
    reports/
  reports/task.json
  reports/task.html
```

The APK index references actual execution workspaces; it does not duplicate large downloads.
Skills and contracts are external dependencies, referenced from the project resource directories.
The manifest records their roots and SHA-256 hashes; no resource directories are copied into a new
execution. Original submitted prompts remain in call records as evidence of actual AI input.
New task IDs include the original subject and a full UUID4. Execution IDs also use UUID4, with
database uniqueness and exclusive directory creation. No timestamp is included. Existing IDs
are preserved. Root task naming does not change when package metadata becomes available.

## Ownership and commands

`preprocess` submits a task by default. `prepare-device --from-run` inherits the source task;
`play --prepared-run` inherits the prepared execution's task. Different APKs in a batch remain
isolated inside that single business task. The same package in another submission is another
task, never an implicit merge. Progress and recognition cache ownership include the business task.

`preprocess`, `emulators` and `observe` accept `--task-id` for explicit attachment to an existing
task. Standalone discovery/observation without this argument creates a separate diagnostic task;
there is no implicit current task. `preprocess --key` provides submission idempotency and cannot
be combined with `--task-id`.

`resume <run_id>` keeps its directory. `resume <run_id> --new-batch` copies the stage input into a
new execution under the same task and pins new code/Skills. It never overwrites the previous
batch or bypasses action reconciliation. Device reservations remain scoped to the source APK
workspace; directory grouping does not grant permission to reuse an instance.

```powershell
uv run game-auto tasks
uv run game-auto task-status <task_id>
uv run game-auto runs
uv run game-auto status <run_id>
```

The task report indexes all executions and APKs, with links to offline traces. It is rebuilt after
stage execution or explicitly with `task-status`. Per-execution success is not whole-task success
or game coverage. The command `status` includes task and execution directory locations.

## Existing data

Opening an older database upgrades its ownership schema after acquiring the execution lock.
A local SQLite backup is saved under `var/migrations/task-layout/`. Grouping follows recorded
`source_run` and `prepared_run` references only. Historical technical task IDs remain aliases;
historical events retain their original IDs and payloads. Execution inputs, action records,
reservations and sessions are retained. Existing progress is assigned to its recorded owning task.

```powershell
uv run game-auto migrate-workspaces
```

The command moves registered `var/runs/<run_id>/` directories into their task's `executions/`
directory under exclusive execution/layout locks. It journals relative source/target paths before
moving, checks both paths remain under the intended workspace, and refuses conflicting copies or
linked source directories. Interrupted relocation can be repeated; it never overwrites a target.
Unregistered directories are reported and left untouched. Already missing historical artifacts
remain missing, rather than being represented as restored evidence.

Original call files, provider traffic and frozen manifests are not rewritten. The resolver maps
legacy absolute artifact references when reading an action's saved observation. Regenerating HTML
reports uses current paths and recorded events without device actions or model calls. Old external
bookmarks need the new location from `status`; new executions use only task-scoped paths.

Code changes still require a new batch: migrating directories does not make old code snapshots
compatible with current code. Removing a task or releasing a device reservation is outside this
command's scope.

## Shared dependency versions

Changing or deleting a referenced Skill, its resources, or a contract blocks resume with
`SNAPSHOT_CHANGED`. Restore the pinned dependency version or use `--new-batch` to pin current resources.
The system does not silently substitute updated instructions. Historical context views show a Skill
as unavailable if its pinned content can no longer be read; they do not present current text as old
evidence. Actual call requests remain inspectable when retained.

Older manifests without external resource roots continue to resolve their existing local copies.
Those original snapshots are neither rewritten nor regenerated automatically. Removing a task folder
does not delete its SQLite history or device reservation, and missing task files are not restored
merely by opening the database.
