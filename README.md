# Game Automation

A modular game-testing framework for exploring reachable interactions and recording the paths,
decisions, actions, and evidence behind them.

APK preprocessing, task intake, Android environment checks, MuMu discovery and screenshots, bound
APK preparation, focused-display observations, independent process plugins, frozen runtime Skills,
Codex integration, and offline trace inspection are implemented. The gameplay runtime adds explicit
installed-APK adoption, protected credential references, guarded actions, checkpoints and scoped
logcat, local guarded routines and hybrid Codex planning. Real tasks verified same-role restoration
and a benign in-game panel path. Native battle and runtime flow reuse have separate settlement,
efficiency and cross-game acceptance gates. See [gameplay](docs/gameplay.md).

## Setup

Use Python 3.12, uv, and Codex CLI 0.163.0. Run commands from the repository root, or pass
`--project <project_root>`. Make the pinned Codex executable available on PATH; keep
machine-specific executable paths out of committed configuration.

```powershell
uv sync --locked
uv run game-auto submit --task contracts/v1/examples/task.json --key task-001
uv run game-auto intake <task_id>
```

For a document, use `submit --document <file.txt>` or `submit --document <file.md>`. Documents must
be UTF-8 and no larger than 200,000 bytes. Document interpretation uses the configured Codex
account; canonical JSON intake needs no model call.

```powershell
uv run game-auto status <run_id>
uv run game-auto provide <run_id> --text "The target package is com.example.game"
uv run game-auto resume <run_id>
uv run game-auto cancel <run_id>
```

Missing information blocks intake and produces questions. Supplements are append-only. Accepted
requirements are immutable; submit a new task to change them. `run <task_id>` starts intake and
environment checks, then blocks at the unimplemented game-launch stage if prerequisites pass.
Environment checks require Android Platform Tools on PATH and an installed target package. Set
`GAME_AUTO_ADB` for a local executable override and `GAME_AUTO_DEVICE_SERIAL` to select a device.
Without a serial, exactly one listed device is required. See
[environment checks](docs/environment.md). Intake or environment readiness does not establish game
coverage.

## APK preprocessing

Submit a private JSON file containing `{"apkUrls": ["https://example.com/game.apk"]}`:

```powershell
uv run game-auto preprocess --input var/inputs/apks.json
uv run game-auto runs --limit 20
uv run game-auto status <run_id>
uv run game-auto resume <run_id> --input var/inputs/apks.json
```

Five APK workers process the list concurrently; additional entries wait in FIFO order. Each APK
receives its own locked workspace, resumable download checkpoints, and compact metadata: `apkName`,
`packageName`, version, size, and SHA-256. Install Android SDK Build Tools for AAPT2, or set
`GAME_AUTO_AAPT2` locally. No model call is required. See [APK preprocessing](docs/preprocessing.md)
for the input contract, recovery behavior, and isolation.

AAPT2 is checked before network probes or downloads. Complete local transfers recover through hash
and ZIP validation; redirects have bounded hops and unread intermediate bodies. Retryable error
responses honor valid server retry waits. Preprocessing has no overall item/batch duration or
transient-attempt cap by default. Network I/O timeouts remain finite; retry backoff and server
cooldowns survive recovery. Positive configuration values can impose explicit duration or attempt
limits.

Before downloads start, the kernel checks the entire batch's disk budget. Unknown sizes use the
configured APK size limit. Insufficient space blocks execution with a warning; status and trace
retain the estimate. Storage settings include a 2 GiB free-space reserve by default.

## MuMu discovery

```powershell
uv run game-auto emulators
uv run game-auto emulators --capture
uv run game-auto emulators --instance <index> --capture
```

Running instances and their ADB endpoints are discovered automatically from MuMu's manager. The
adapter queries readiness and optional screenshots without changing applications or emulator
settings. Root is not required. See [MuMu discovery](docs/emulators.md) for selection and local
overrides.

## Device preparation and observations

```powershell
uv run game-auto observe --instance <index>
uv run game-auto prepare-device --from-run <preprocessing_run_id> --apk-index 0 --instance <index>
```

Select a dedicated, already-running instance for each ready APK workspace. The kernel persists a
unique reservation, checks Android disk capacity, and verifies installation against the source
SHA-256. Uncertain installations are inspected before recovery and never replayed blindly. The
observer follows the focused Android display and checks rotation and screenshot dimensions. Instance
creation, cleanup, input control, and application launch are not part of these commands. See
[device preparation](docs/device-preparation.md) for ownership, recovery, and acceptance limits.

## Trace inspection

For gameplay, initialize a protected credential reference and use a prepared device workspace:

```powershell
uv run game-auto credentials default
uv run game-auto play --prepared-run <device_run> --goal login-and-interact
uv run game-auto play --prepared-run <device_run> --goal explore
uv run game-auto play --prepared-run <device_run> --goal advance-battle
uv run game-auto resume <run_id> --new-batch
uv run game-auto report <run_id>
uv run game-auto compare-games <first_run> <second_run>
```

Use `--adopt-existing` during preparation to preserve a matching installed game's data. Gameplay has
no default overall limit; optional `--max-turns` bounds one invocation. Unknown outcomes require
observation/reconciliation. `--new-batch` retains task identity while pinning updated code/Skills.

```powershell
uv run game-auto trace <run_id> --follow
uv run game-auto inspect <run_id> --span <span_id>
uv run game-auto context <run_id>
uv run game-auto export-trace <run_id> --format html
```

One business task owns one directory under `var/tasks/<task_id>/`. Preparation and gameplay inherit
the task from their source execution. Each execution stores a dependency manifest, calls, artifacts and
reports under `executions/<run_id>/`; resume keeps that directory and `--new-batch` adds a child
execution. The task's `reports/task.html` indexes all stages, and `apks/index.json` locates isolated
APK workspaces without copying downloads.
Skills and contracts are shared external dependencies under `skills/` and `contracts/v1/`. Executions
record their paths and content hashes, without copying those directories. Changed or missing
dependencies block resume until the pinned version is restored or a new batch is created.

```powershell
uv run game-auto tasks
uv run game-auto task-status <task_id>
uv run game-auto migrate-workspaces
```

Use `uv run game-auto cleanup --missing` to preview removal of database records whose task folders
were deleted manually; add `--apply` to delete those records and compact SQLite. `--task-id` selects
specific tasks, while `--all` selects all task data. Credentials, migration backups and shared
resources are preserved. See [cleanup](docs/cleanup.md) for scope and interruption recovery.

Use `--task-id <task_id>` with `preprocess`, `emulators` or `observe` to attach those operations to
an existing task. Without it, a standalone submission creates its own task. Legacy migration groups
only recorded source relationships, retains execution IDs and raw evidence, and backs up the
database locally. See [task workspaces](docs/task-workspaces.md).

The offline HTML report shows recorded context and execution without invoking a model or device.
Public reasoning summaries and token usage remain unavailable when the provider does not return
them.

## Project layout

| Location               | Responsibility                                           |
| ---------------------- | -------------------------------------------------------- |
| `src/game_automation/` | CLI, kernel, prompting, event projection, reports, storage, and SDK |
| `plugins/`             | Independent capability implementations                   |
| `skills/`              | Runtime AI instructions                                  |
| `contracts/v1/`        | Shared JSON schemas                                      |
| `config/`              | Application, plugin, and Skill configuration             |
| `docs/`                | Architecture and protocol documentation                  |
| `var/`                 | Private local runtime data, excluded from Git            |

See [source layout](docs/project-layout.md) for module ownership, report assets and package rules.
See [runtime data](docs/runtime-data.md) for inputs, logs, temporary files and persistent state.

## Development

Follow [AGENTS.md](AGENTS.md): English maintained content, readable and minimal code, UTF-8 without
BOM, and LF line endings. Keep private operational configuration out of tracked files. uv.lock pins
dependencies. Process isolation is not a sandbox.

For each increment, check out a `codex/<feature>` branch from `master`. Commit the completed work,
merge it into `master`, delete the merged feature branch, and publish `master` to the configured
push destinations.

Verification is compilation only. Do not add or run mock or smoke tests.

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path (Get-Location) "var/cache/bytecode"
uv run python -m compileall -q src plugins
node --check src/game_automation/reporting/web/dashboard.js
```

Compilation does not verify runtime behavior. See [architecture](docs/architecture.md),
[task intake](docs/task-intake.md), [environment checks](docs/environment.md),
[protocol](docs/protocol.md), [debugging](docs/debugging.md), and
[context insights](docs/context-insights.md).
