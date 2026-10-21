# Game Automation

An evidence-first game testing framework focused on interaction coverage and recorded paths. The current release provides M0 infrastructure: process plugins, persistent tasks and runs, explicit skill injection, Codex App Server integration, and context insights with offline trace browsing.

Document parsing, Android control, perception, exploration algorithms, and game coverage reports are planned modules. `demo` uses fixtures. `codex-smoke` performs real inference against synthetic observations and does not control a device.

## Quick start

Run from the repository root in PowerShell:

```powershell
uv sync --locked
uv run game-auto demo
```

The command prints task_id, run_id, status, and the absolute path of reports/trace.html. The self-contained report offers turn selection, composition, frozen skill content, input/output search, provider usage, and the raw call tree. Its local script only filters saved data; it makes no network or device calls.

```powershell
uv run game-auto context <run_id>
uv run game-auto context <run_id> --json
uv run game-auto trace <run_id> --follow
uv run game-auto inspect <run_id> --span <span_id>
uv run game-auto export-trace <run_id> --format html
uv run game-auto status <run_id>
```

Export also writes context.json, a deterministic read model derived from existing evidence. Full model context and internal model-request counts remain unavailable. Text estimates are labeled separately from reported token counters.

## Codex checks

Codex CLI **0.162.1** must be available on PATH, or its absolute path configured in config/application.toml. Existing supported Codex authentication is reused without copying tokens or modifying global configuration.

```powershell
# Version and handshake only; no inference
uv run game-auto codex-probe

# Real inference; uses account quota, returns a stop decision, no device actions
uv run game-auto codex-smoke
```

Each AI turn explicitly receives the frozen project-base and stage skills. The configured account's default model is used unless a model is specified. Public events, summaries when supplied, usage, and structured decisions are recorded. A successful handshake alone does not verify inference or real-game capability.

## Tasks and recovery

The initial interface accepts canonical JSON matching contracts/v1/task.schema.json. Natural-language document parsing is not implemented yet.

```powershell
uv run game-auto submit --task contracts/v1/examples/task.json --key test-001
uv run game-auto run <task_id>
uv run game-auto provide <run_id> --text "Additional task information"
uv run game-auto resume <run_id>
uv run game-auto cancel <run_id>
```

Real game runs currently become blocked because task.intake is unavailable. The same submission key and task return the existing task; changed contents require a new key. `run` creates a new execution batch. `resume` continues queued/blocked runs or reconciles a running status left by a crashed process. Terminal runs are preserved.

Source skill edits do not alter old snapshots. Framework or plugin code changes require a new run. Unknown action outcomes cannot be replayed automatically; there is no force-replay switch. Supplements are append-only.

Exit codes: 0 for successful commands/runs, 2 for blocked/failed runs or command errors, and 130 when interrupted. A queued cancellation is applied when that run next executes.

## Layout

| Location | Responsibility |
|---|---|
| skills/ | Runtime AI instruction sources, separate from AGENTS.md |
| contracts/v1/ | Language-neutral schemas and examples |
| src/game_automation/kernel/ | Workflow, plugin lifecycle, capability routing, policies |
| src/game_automation/prompting/ | Skill snapshots and deterministic input assembly |
| src/game_automation/observability/ | Read-only projections, context browser, trace export |
| src/game_automation/storage/ | Authoritative state, events, and artifacts |
| plugins/agent_codex/ | Provider-specific protocol and strict-output adaptation |
| tests/fixtures/ | Explicit synthetic plugins/providers; no devices |
| var/ | Local database, snapshots, reports, references, and test files; ignored by Git |

See [plugin status](plugins/README.md) for planned capabilities.

## Development

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests do not call a real model or device. Temporary data lives in var/pytest. uv.lock pins runtime and development dependencies. Plugins declare independent dependencies and may use separate interpreters. Process isolation is not a security sandbox.

All maintained project content is English, encoded as UTF-8 without BOM with LF endings. User/game data may contain arbitrary Unicode; fixtures use escaped Unicode literals to exercise that behavior. This is a source deployment; run from the root or use `game-auto --project <root> ...`.

## Repository synchronization

The project is published to both repositories:

- Aliyun: https://codeup.aliyun.com/smwl/yss-workspace/Game-Automation.git
- GitHub: https://github.com/WartDipara/GA.git

origin fetches from Aliyun and has both URLs configured for pushing. github is also available as a separate remote for verification or recovery. Initialize another checkout with:

```powershell
git remote set-url origin https://codeup.aliyun.com/smwl/yss-workspace/Game-Automation.git
git remote add github https://github.com/WartDipara/GA.git
git remote set-url --add --push origin https://codeup.aliyun.com/smwl/yss-workspace/Game-Automation.git
git remote set-url --add --push origin https://github.com/WartDipara/GA.git
```

Run setup once; for an existing checkout inspect `git remote -v` first. `git push origin master` publishes master to both destinations. Verify with `git ls-remote --heads origin master` and `git ls-remote --heads github master`. Two-server publication is not atomic: if one push fails, resolve its error and retry that remote without force-pushing.

See [architecture](docs/architecture.md), [protocol](docs/protocol.md), [debugging](docs/debugging.md), [context insights](docs/context-insights.md), and [validation](docs/validation.md).
