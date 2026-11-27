# Game Automation

Run all commands from the repository root.

## Requirements

- Windows, Python 3.12 and uv.
- Node.js from `.node-version` and npm from `package.json` (`devEngines`).
- Codex CLI on PATH, or selected with `[codex].executable`, signed in locally.
- MuMu with a dedicated, already-started Android instance for each APK.
- Android SDK Build Tools (`aapt2`); ADB on PATH or supplied by MuMu.

## Install

```powershell
uv sync --locked
npm ci
npm run build
```

Run dependency commands from the repository root. npm workspaces share `package-lock.json`.
Python and its plugin processes share the environment resolved by `pyproject.toml` and `uv.lock`.
React renders the trace workspace, Ajv validates TypeScript contracts, and Drizzle owns control
plane database access. The workflow and capability boundaries use local JSON-RPC.

The build detects the selected Codex CLI version, generates its App Server types, and records the
version in the artifacts. After changing the CLI, rebuild and use `--new-batch` for resumed work.

## Configure

1. Check `config/application.toml` for budgets, timeouts and workflow stages.
   Each gameplay agent has a named table such as `[gameplay.agents.agent_tom]`.
   Set its `job_title`, `describ`, `model`, and `reasoning_effort` there.
   New batches use the edited settings.
   `[gameplay].max_turns = 0` omits the optional lead-turn cap. The workflow cycle limit and
   no-progress checks still apply.
2. Keep enabled capabilities in `config/plugins.toml` and Skill bindings in
   `config/skills.toml`.
3. Save test credentials using the hidden local prompts:

```powershell
uv run --locked game-auto credentials default
```

4. Create a private UTF-8 file at `var/inputs/apks.json`:

```json
{
  "apkUrls": ["https://example.com/game.apk"]
}
```

5. Discover the indices of your running MuMu instances:

```powershell
uv run --locked game-auto emulators
```

Optional local executable overrides: `GAME_AUTO_ADB`, `GAME_AUTO_AAPT2` and
`GAME_AUTO_MUMU_MANAGER`. Keep machine paths and credentials out of tracked files.

## Run

For a login test using `var/inputs/apks.json` and the `default` credential profile, pass the
running MuMu instance index:

```powershell
npm run game -- 0
```

For multiple APKs, pass one distinct index per URL in the same order, for example
`npm run game -- 0 1`. Replace the example indices with those reported by `game-auto emulators`.
The shortcut accepts additional workflow options, such as `npm run game -- 0 --goal explore`.

The full workflow command is available for explicit inputs and options:

```powershell
uv run --locked game-auto workflow --input var/inputs/apks.json --instances <index> --goal explore
```

Use `--credentials <profile>` for another vault profile or `--max-turns <count>` to override
the planning limit.
For downloads only, use `game-auto preprocess --input var/inputs/apks.json`.

## Inspect and recover

Use the execution ID printed by the command:

```powershell
uv run --locked game-auto status <run_id>
uv run --locked game-auto exploration <run_id> --format json
uv run --locked game-auto resume <run_id> --input var/inputs/apks.json
uv run --locked game-auto cancel <run_id>
uv run --locked game-auto report <run_id>
uv run --locked game-auto serve-trace <run_id> --port 0
```

After updating code or Skills, use `resume <run_id> --new-batch` to pin the updated dependencies.
Use `report <run_id>` to rebuild missing reports without running the game again.

## View traces and history

Start the independent viewer in another terminal:

```powershell
npm run trace
```

Open the printed URL (default port: 8767). Select a task, then **Execution overview**.
While running, open **Live monitor** for the incremental activity chart and dated event table.
After stopping, use **Call trace**, **Model context**, **Actions**, **Elements** and **Outcome**.
Detailed reports do not poll; reload to view a later publication.
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
