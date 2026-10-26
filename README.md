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

Use `--credentials <profile>` for another vault profile, `--max-turns 30` to bound
one invocation, or `--goal login-and-interact` for login and one benign interaction.
For downloads only, use `game-auto preprocess --input var/inputs/apks.json`.

## Inspect and recover

Use the execution ID printed by the command:

```powershell
uv run game-auto status <run_id>
uv run game-auto resume <run_id> --input var/inputs/apks.json
uv run game-auto cancel <run_id>
uv run game-auto report <run_id>
uv run game-auto serve-trace <run_id> --port 0
```

Run the trace service in another terminal and open its printed URL. After updating
code or Skills, use `resume <run_id> --new-batch` to pin the updated dependencies.
Reports and evidence are stored under `var/tasks/<task_id>/`.

See [workflow options](docs/workflow.md), [gameplay](docs/gameplay.md),
[device ownership](docs/device-preparation.md), [troubleshooting](docs/debugging.md)
and [task cleanup](docs/cleanup.md).
