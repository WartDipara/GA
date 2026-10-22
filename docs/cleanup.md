# Task cleanup

Use the CLI from the project root. Selection only previews removal; `--apply` permanently deletes
the selected task data. No backup is created by cleanup, since its purpose is to release space.

```powershell
# Folders were already deleted manually: inspect and remove their remaining database history.
uv run game-auto cleanup --missing
uv run game-auto cleanup --missing --apply

# Remove one task, including every execution and its files.
uv run game-auto cleanup --task-id <task_id>
uv run game-auto cleanup --task-id <task_id> --apply

# Remove all task data, including unregistered task/execution folders.
uv run game-auto cleanup --all
uv run game-auto cleanup --all --apply
```

Repeat `--task-id` to select multiple tasks. Historical technical task IDs resolve to their business
task, so removal always covers the whole task. `--missing` selects only tasks with neither a task
folder nor a retained legacy execution folder; an existing empty task folder is not selected.
An input referenced by another retained task cannot be deleted independently.

The preview lists task IDs, execution count, relative paths, logical file bytes and current SQLite
file size. File bytes are not a prediction of SQLite savings. Applying removes task submissions,
aliases, execution inputs, runs, events, sessions, actions, progress and device bindings. SQLite
`VACUUM` and WAL truncation then reclaim unused database space; output includes the resulting
database file size. Compaction may need temporary disk space.

Filesystem removal is limited to task children under `var/tasks/` and execution children under
legacy `var/runs/`. Target paths are checked before recursive deletion; symlinks and Windows
junctions are rejected. `--all` includes unregistered children in those two locations. Cleanup
never uninstalls games, changes emulator data or performs a device action.

Credentials in `var/secrets/`, migration backups, shared Skills/contracts, other local inputs,
development files and logs elsewhere in `var/` are preserved. Migration backups can retain deleted
historical data and continue occupying space; this command does not erase them.

## Interruption and ownership

Cleanup acquires the same execution and layout locks as workflow operations. It cannot delete data
while a framework executor owns the execution lock. Close external database viewers before applying
cleanup so that SQLite can obtain the locks required for compaction.

Filesystem deletion and a database commit cannot be a single atomic operation. Before deleting,
cleanup persists its fixed selection under `var/cleanup/pending.json`. File deletion happens before
the transactional database deletion. If a file is locked, a database operation fails, or the process
exits, the intent remains and normal framework commands are blocked. Partial file deletion is
irreversible; database records remain until that deletion phase finishes.

```powershell
# Inspect an interrupted cleanup, then finish exactly its original scope.
uv run game-auto cleanup
uv run game-auto cleanup --apply
```

Do not add selection flags when resuming a pending cleanup. Retry is idempotent: missing files and
already deleted records are accepted. The intent is removed only after file removal, database
deletion and compaction have all completed. Do not delete the pending intent to bypass recovery.
If compaction fails, close other database clients, free temporary space and retry the same command.
