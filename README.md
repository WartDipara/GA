# Game Automation

A modular game-testing framework for exploring reachable interactions and recording the paths,
decisions, actions, and evidence behind them.

Task intake, independent process plugins, frozen runtime Skills, Codex integration, and offline
trace inspection are implemented. Android control, visual recognition, exploration, and game
coverage reporting remain planned capabilities.

## Setup

Use Python 3.12, uv, and Codex CLI 0.162.1. Run commands from the repository root, or pass
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
requirements are immutable; submit a new task to change them. `run <task_id>` starts the full
workflow, which currently blocks at the unimplemented environment-check stage. Intake success does
not establish game coverage.

## Trace inspection

```powershell
uv run game-auto trace <run_id> --follow
uv run game-auto inspect <run_id> --span <span_id>
uv run game-auto context <run_id>
uv run game-auto export-trace <run_id> --format html
```

Each run stores its inputs, frozen Skills, calls, and reports locally under `var/runs/<run_id>/`.
The offline HTML report shows recorded context and execution without invoking a model or device.
Public reasoning summaries and token usage remain unavailable when the provider does not return
them.

## Project layout

| Location               | Responsibility                                           |
| ---------------------- | -------------------------------------------------------- |
| `src/game_automation/` | CLI, kernel, prompting, tracing, storage, and plugin SDK |
| `plugins/`             | Independent capability implementations                   |
| `skills/`              | Runtime AI instructions                                  |
| `contracts/v1/`        | Shared JSON schemas                                      |
| `config/`              | Application, plugin, and Skill configuration             |
| `docs/`                | Architecture and protocol documentation                  |
| `var/`                 | Private local runtime data, excluded from Git            |

## Development

Follow [AGENTS.md](AGENTS.md): English maintained content, readable and minimal code, UTF-8 without
BOM, and LF line endings. Keep private operational configuration out of tracked files. uv.lock pins
dependencies. Process isolation is not a sandbox.

Verification is compilation only. Do not add or run mock or smoke tests.

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path (Get-Location) "var/bytecode"
uv run python -m compileall -q src plugins
node --check src/game_automation/observability/dashboard.js
```

Compilation does not verify runtime behavior. See [architecture](docs/architecture.md),
[task intake](docs/task-intake.md), [protocol](docs/protocol.md), [debugging](docs/debugging.md),
and [context insights](docs/context-insights.md).
