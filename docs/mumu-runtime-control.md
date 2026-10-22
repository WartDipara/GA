# MuMu runtime control requirements

These requirements govern target-aware runtime control. Existing `observe` reads the focused
display; it does not provide a target-process monitor, automatic application return, a continuous
log collector, or keymap parsing. Operator actions and their local evidence do not register those
capabilities as automatic workflow features. Keep each capability's acceptance status separate.

## Target identity and liveness

Pin the MuMu instance identity and ADB serial before identifying the target package for Android
user 0. Keep these facts separate:

| Fact                      | Evidence                                                                         |
| ------------------------- | -------------------------------------------------------------------------------- |
| Main process exists       | Package ownership, PID, process state, boot ID and `/proc/<pid>/stat` start time |
| Auxiliary processes exist | Package-owned services/processes, reported separately from the main process      |
| Game task exists          | Activity/task records associated with the package across all displays            |
| Game is focused           | Focused component and logical display ID from window-manager state               |
| Game is responsive        | Additional application/UI evidence; process existence alone is insufficient      |

Use package-manager UID attribution, the process table, and ActivityManager package membership to
identify the main process and workers. Do not rely only on a substring match, a cached PID, or the
existence of any process in the package family. A downloader surviving the main process does not
prove the game is healthy. PID reuse and emulator reboot invalidate an earlier process identity.
Failed or unauthorized queries mean unknown, not dead. Zombie processes are not healthy processes.

Read activity/task records on every logical display. A browser opened by a game can become focused
on another display while the original game process and task survive. An embedded WebView may keep
the same package and use an isolated renderer with another UID. Attribute isolated processes only
when ActivityManager supplies ownership evidence; do not collect every WebView process.

Useful read-only observations include:

```text
adb -s <serial> shell ps -A -o PID,UID,NAME,ARGS
adb -s <serial> shell dumpsys activity activities
adb -s <serial> shell dumpsys activity processes
adb -s <serial> shell dumpsys window
adb -s <serial> shell cat /proc/sys/kernel/random/boot_id
adb -s <serial> shell cat /proc/<pid>/stat
```

## Return to the running game

Observe the target process identity, task, and current display before taking an action. Keep the
game task reference when another application becomes focused. MuMu exposes this provider command:

```text
MuMuManager.exe control -v <instance_index> app launch -pkg <package>
```

The command can launch an application if it is absent, so it is not an unconditional resume API. For
a return operation, require an existing target process and task first; persist intent, issue the
provider command, then recheck focus, task, process start identity, and screenshot. A successful
manager response alone does not prove return. A changed process identity must be reported as a
restart, not a successful uninterrupted return. Do not use force-stop, clear data, task locking, or
a guessed Android display as a shortcut. If the target vanished, apply an explicit launch/recovery
policy and report the interruption rather than silently presenting a restart as a return.

## Game log collection

Use `adb logcat` for Android application logs. MuMu's manager log command and Windows emulator logs
are different diagnostic sources and must not be merged into the game's primary log stream. Check
the selected device's `logcat --help`; filter support varies with the image.

```text
adb -s <serial> shell logcat -b main,crash -v threadtime,uid --pid=<game_pid> -t <record_limit>
adb -s <serial> shell logcat -b main,crash -v threadtime,uid --uid=<game_uid> -t <record_limit>
```

A UID filter can include main and worker processes, but shared UIDs require additional ownership
checks. A PID filter follows only one process lifetime. Record which processes and time interval
were admitted; refresh identities across restarts and bound collection to the observed lifetime. An
empty filtered stream means no matching records were observed, not a broken collector or an
error-free application. Never clear the device log buffers as part of capture.

Keep system crash/ANR evidence in a separate correlated stream: ActivityManager, debuggerd, or
renderer messages may be important even though their emitter is not the game PID. Conversely, source
filtering cannot remove emulator or SDK code logging inside the game process without additional
attribution. Preserve such records and classify their source when known; do not promise noise-free
logs or drop messages merely because their text mentions the emulator. A future continuous collector
needs bounded files, a storage budget, process-lifetime segmentation, and explicit gap records
before it can be accepted for long-running workflows.

## Keyboard hints and coordinate mapping

MuMu's Windows key hints are outside the Android framebuffer. Prefer the ADB screenshot for game
interaction discovery; a host-only key label must not become a game button or a coverage item. Read
the selected package's active keymap selector and then its referenced scheme, rather than choosing
an arbitrary available scheme. Keep profile paths and hashes in local evidence only.

The locally exposed scheme format includes `Current`, `keymaps`, `key.text`, `type`,
`rel_work_position`, and `icon.visibility`. For a `Click` entry with relative coordinates in the
current game viewport, derive touch coordinates from `rel_x * width` and `rel_y * height`. The icon
position and action position are separate fields and need not match. Recheck viewport dimensions,
rotation, active scheme, and display after switching applications. A Windows-window click also needs
viewport origin, scale, and any letterboxing; Android pixel coordinates are not desktop coordinates.
Unsupported mapping types or ambiguous scheme selection remain unresolved.

Sending an Android key event for the letter A does not invoke MuMu's Windows A-to-touch mapping. An
automation action may instead use the resolved touch point, subject to kernel permission and fresh
observation. Do not rewrite the user's scheme, trigger an unreviewed macro, or press an unknown
mapped key merely to inspect the configuration.

See [device preparation](device-preparation.md) for the implemented interface boundaries and
[Android logcat](https://developer.android.com/tools/logcat) for Android logging options.
