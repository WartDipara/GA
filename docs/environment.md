# Android environment checks

The environment plugin implements `environment.check` as an independent JSON-RPC process. The kernel
calls it after accepting a task. Development verification is compilation only; no device acceptance
is claimed.

## Prerequisites and selection

Install Android SDK Platform Tools and enable debugging on the intended device. Complete any
debugging authorization on the device. The task target must be an already installed Android package
identifier, such as `com.example.game`. APK installation is outside this increment.

By default the plugin resolves `adb` on PATH. Local environment variables can override selection:

| Variable                  | Purpose                                   |
| ------------------------- | ----------------------------------------- |
| `GAME_AUTO_ADB`           | ADB executable name or absolute path      |
| `GAME_AUTO_DEVICE_SERIAL` | Exact serial returned by `adb devices -l` |

Do not commit actual machine paths or serials. The kernel reads these variables for each execution
or resume and persists their effective values in the capability input. This permits correcting a
local connection without changing accepted task requirements. The per-command timeout is frozen from
`environment.command_timeout_seconds` in application configuration; its allowed range is 1 through
60 seconds.

Without a selected serial, the device list must contain exactly one entry, including offline or
unauthorized entries. With a serial, that exact entry must exist. Only the `device` state permits
further queries. The plugin never chooses the first of several devices or grants authorization.

## Check sequence

1. Confirm Android and validate the target as a package identifier before any device shell query.
2. Resolve the executable and collect `adb version`.
3. Collect `adb devices -l`, select exactly one device, and inspect its connection state.
4. Query `adb -s <serial> shell getprop sys.boot_completed`; require `1`.
5. Query `adb -s <serial> shell pm path <package>`; require successful output containing package
   paths.

Commands use argument arrays. Only the validated package identifier enters a device shell command.
Checks stop at the first blocker. No game is installed, launched, or clicked, and no permissions are
changed. ADB may start its local server when queried. Package availability refers to the package
manager's default user context; profile-specific checks and game permissions remain future work.

Command syntax and device states follow the
[official ADB documentation](https://developer.android.com/tools/adb).

## Results, tracing, and recovery

Results contain `status`, `device_serial`, `package_paths`, and ordered `checks`. Each check records
its name, status, diagnostic code, message, and related command identifier when available. The host
validates results before recording a successful capability call. A completed diagnostic call can
still report a blocked environment.

Each query has persisted start and completion events with arguments, exit code, stdout, stderr, and
elapsed time. A timeout or failure to start records incomplete evidence with unavailable output set
to null. If the plugin or host is interrupted, the enclosing call is incomplete; no missing command
result is inferred. Failed persistence acknowledgements stop subsequent queries.

`game-auto status <run_id>` includes the latest completed environment result. Use `trace`,
`inspect`, or the offline HTML export to view command evidence and earlier attempts. These records
stay under local `var/`; they can contain device identifiers and local paths and are not uploaded
automatically.

When blocked, correct the local prerequisite and run `game-auto resume <run_id>`. Accepted intake is
reused, but every environment query runs again and creates new evidence. A previous ready result is
not reused as current device state. A ready check currently advances to `launch` and blocks with
`DEVICE_NOT_IMPLEMENTED`. It does not establish that the game can start or that interactions work.
