# Android device preparation

This increment connects ready preprocessing items to dedicated, already-running MuMu instances. It
implements instance reservations, single-APK installation, package fingerprint verification, and
focused-display observations. These code paths have compilation verification only; the earlier
operator observations do not establish acceptance of this installation workflow.

## Prepare one APK

Choose an existing instance dedicated to the selected APK workspace. Do not select a personal
instance or one another operator is using. The framework does not create, clone, wipe, or reset
instances in this increment.

```powershell
uv run game-auto status <preprocessing_run_id>
uv run game-auto prepare-device --from-run <preprocessing_run_id> --apk-index 0 --instance <index>
uv run game-auto status <preparation_run_id>
uv run game-auto resume <preparation_run_id>
```

`--apk-index` is zero-based, matching preprocessing status entries. A ready item may be selected
even if another item in its batch is blocked. Its stored metadata, local APK size, and SHA-256 must
still match. Installation uses that existing APK without duplicating it into another workspace.
When a new batch reuses a completed download, the original manifest must still match its recorded
hash. The retained APK metadata is validated against the consuming version's contract, then the
APK bytes are verified before installation. Reading this historical output does not reactivate the
old producer or require all of its former shared contracts to remain current. Executing an old
batch still requires its pinned dependencies to match.

The kernel reserves one instance for one preprocessing workspace. Both sides have database unique
constraints; a different APK cannot claim the same instance, and the same workspace cannot claim
another instance. Reservations persist across failures, cancellation, and successful preparation. A
new execution batch for the same workspace may reuse its reservation and reconcile earlier actions,
including after a runtime upgrade. Earlier actions and failures remain in their original run. A
different preprocessing workspace cannot claim an occupied instance, even for an identical APK.
Automatic release, rebinding and instance creation remain unavailable.

For explicitly reusing a dedicated instance after its prior execution has stopped, preview and
release the original reservation through the kernel:

```powershell
uv run game-auto release-device <reservation_run_id>
uv run game-auto release-device <reservation_run_id> --apply
```

The command requires exclusive execution ownership and rejects active runs or plugin calls. It
only removes the reservation: it does not operate Android, remove application data, delete task
history, or mark unresolved actions successful. Its preview and committed event list unresolved
actions, which remain owned by their original APK workspace. A new workspace cannot reconcile or
replay those actions. Returning to the original workspace still requires observing and reconciling
its pending actions. The release and report request commit together; report-only repair remains
available. Subsequent preparation must resolve the real instance and verify the installed APK.

The adapter derives instance identity from the resolved manager location, instance index, and MuMu's
creation timestamp. Session requests pin that identity and the advertised ADB endpoint. Each Android
capability resolves the instance again before using its explicit serial. A replaced instance or
changed endpoint blocks execution rather than silently selecting another device. This identifies the
manager's instance record; it cannot detect an external snapshot restoration that preserves that
record. Exclusive use and a clean starting environment remain caller duties.

## Installation and recovery

Android user 0 is the current supported installation target. Existing target packages without a
matching framework action record block preparation. The installer does not replace or uninstall
them, clear app data, grant permissions, enable root, or launch the application.

Before dispatch, the kernel estimates Android `/data` capacity as APK bytes plus twice the ZIP's
expanded bytes plus a configurable reserve, initially 1 GiB. The estimate and observed available
space are persisted, and the plugin checks free space again before installation. This is a
conservative admission estimate, not an upper bound on Android compilation, later game downloads, or
consumption by external processes. The separate host-disk reserve also applies.

The workflow has no overall wall-clock deadline. Individual discovery and observation commands have
finite deadlines. An installation command has a separate 900-second deadline by default;
`device.install_timeout_seconds` adjusts it without changing the workflow lifetime. A timeout stops
the owned client process and blocks the run; it does not prove Android stopped installing. No
installation is retried automatically.

The host first persists an action intent. The plugin performs read-only preconditions, then requests
`host.action` authorization immediately before the install command. The kernel commits the action as
`unknown` before acknowledging authorization. Without this acknowledgement no install is sent.

| Action state | Meaning                                                           | Recovery                                                 |
| ------------ | ----------------------------------------------------------------- | -------------------------------------------------------- |
| prepared     | Intent saved; dispatch was not authorized                         | Close the undispatched attempt and recheck prerequisites |
| unknown      | Dispatch authorized; result may be missing                        | Inspect the installed package; never resend blindly      |
| executed     | ADB reported installation success                                 | Inspect the installed APK before accepting preparation   |
| rejected     | No dispatch occurred, or Android explicitly rejected installation | A later explicit resume may try again after inspection   |
| verified     | Installed base APK matches the source SHA-256                     | Recheck the package and reuse it without installation    |

Successful preparation requires a fresh package query and SHA-256 of the installed base APK,
matching the immutable source. Missing or different content after an uncertain dispatch remains
blocked, including in a new execution batch. ADB transport errors never count as explicit Android
rejections. Split-package installation is unsupported. An unreadable installed APK blocks
verification rather than inferring success from the package name or version.

Preparation reports retain `apkName`, `packageName`, version, size, fingerprint, source workspace,
instance identity, and serial. Successful installation does not establish launch or game coverage.

## Observe the current display

```powershell
uv run game-auto observe --instance <index>
```

MuMu can place applications on separate Android logical displays. The observer reads
`mTopFocusedDisplayId`, finds that display's focused component, current dimensions, and rotation,
then captures a screenshot. It reads window state again after capture. A changed display, component,
rotation, or size rejects the observation, as do mismatched PNG dimensions. There is no guessed
default display, cached coordinate system, or automatic capture retry.

The current MuMu capture path uses its default `screencap -p` behavior, observed to follow the
focused application. Dimension and state checks correlate the capture; they are not visual proof of
window identity when windows have identical sizes. Logical display IDs are not passed as physical
`screencap -d` IDs. Unsupported window-manager output blocks explicitly. Coordinates, input control,
Windows window activation, ongoing orientation monitoring, and game launch remain later increments.

Observations expose a unique observation ID, logical display ID, package, activity, dimensions,
rotation in degrees, and locally verified screenshot reference. `device.observed`, package queries,
capacity checks, action records, and all adapter commands appear in the offline trace. No model call
or screenshot upload is involved.

If a previously verified installation is now absent, preparation records that fresh observation
and creates a new installation intent under its normal authorization checks. The settled historical
action is preserved. Unknown or executed installations still require reconciliation before another
installation; a missing package does not make those uncertain actions safe to replay.

References: [Android Debug Bridge](https://developer.android.com/tools/adb).
