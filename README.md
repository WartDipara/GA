# Game Automation

Run all commands from the repository root.

## Requirements

- Windows, Python 3.12 and uv.
- Node.js from `.node-version` and npm.
- Codex CLI 0.163.0, installed on PATH and signed in locally.
- MuMu with a dedicated, already-started Android instance for each APK.
- Android SDK Build Tools (`aapt2`); ADB on PATH or supplied by MuMu.

## Install

```powershell
uv sync --locked
npm ci
npm run build
```

## Configure

1. Check `config/application.toml` for budgets, timeouts and workflow stages.
   `[codex]` selects `model = "gpt-6-luna"` and `reasoning_effort = "medium"`.
   `[gameplay].max_turns = 0` allows unlimited planning; use a positive limit to bound it.
2. Keep enabled capabilities in `config/plugins.toml` and Skill bindings in
   `config/skills.toml`.
3. Save test credentials using the hidden local prompts:

```powershell
uv run game-auto credentials default
```

4. Create a private UTF-8 file at `var/inputs/apks.json`:

```json
{
  "apkUrls": ["https://example.com/game.apk"]
}
```

5. Discover the indices of your running MuMu instances:

```powershell
uv run game-auto emulators
```

Optional local executable overrides: `GAME_AUTO_ADB`, `GAME_AUTO_AAPT2` and
`GAME_AUTO_MUMU_MANAGER`. Keep machine paths and credentials out of tracked files.

## Run

Assign one distinct running instance per URL, in the same order:

```powershell
uv run game-auto workflow --input var/inputs/apks.json --instances <index> --goal explore
```

Use `--credentials <profile>` for another vault profile, `--max-turns <count>` to override
the planning limit, or `--goal login-and-interact` for login and one benign interaction.
For downloads only, use `game-auto preprocess --input var/inputs/apks.json`.

## Inspect and recover

Use the execution ID printed by the command:

```powershell
uv run game-auto status <run_id>
uv run game-auto exploration <run_id> --format json
uv run game-auto resume <run_id> --input var/inputs/apks.json
uv run game-auto cancel <run_id>
uv run game-auto report <run_id>
uv run game-auto serve-trace <run_id> --port 0
```

After updating code or Skills, use `resume <run_id> --new-batch` to pin the updated dependencies.
Use `report <run_id>` to rebuild missing reports without running the game again.

## View traces and history

Start the independent viewer in another terminal:

```powershell
npm run trace
```

Open the printed URL (default port: 8767). Select a task, then **Execution overview**.
Use **Call trace**, **Model context**, **Actions**, **Elements** and **Outcome** to inspect it.
Calls link to parent calls and retained inputs/outputs; actions link to screenshots.
Use **Back** to return, or **Task overview** / **Task history** to change execution or task.

New tasks appear automatically with a three-second refresh. Reports and indexes are generated
after success, failure, cancellation or a blocked checkpoint. Press `Ctrl+C` in the viewer terminal
to stop the viewer; the workflow continues independently.

Expand the sidebar's **Task → Executions → Reports** hierarchy to navigate retained records.
Open **Appearance** to choose a workspace style, light/dark mode, text size and motion.
Gothic, Corporate Clean, Editorial, Neo-Brutalist, Bento Grid and Soft UI are included.
Preferences persist in this browser across reports and tabs.
Edit `config/ui-theme.json` and run `npm run build` to change the shared design defaults.

To open a specific execution's task using a free port, use this alternative in PowerShell:

```powershell
npm.cmd run trace -- <run_id> --port 0
```

Events are stored in `var/state.sqlite`; inputs, screenshots and reports are under
`var/tasks/<task_id>/executions/<run_id>/`. See [trace workspace](docs/trace-workspace.md) for details.

See [workflow options](docs/workflow.md), [gameplay](docs/gameplay.md),
[device ownership](docs/device-preparation.md), [troubleshooting](docs/debugging.md)
and [task cleanup](docs/cleanup.md).
