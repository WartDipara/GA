# MuMu discovery

MuMu is the initial emulator provider. This increment discovers existing instances, connects ADB,
checks Android readiness, and optionally captures screenshots. Instance creation, cloning, reboot,
general input control, and game exploration remain separate capabilities. Bound installation and
focused-display capture are described in [device preparation](device-preparation.md).

```powershell
uv run game-auto emulators
uv run game-auto emulators --capture
uv run game-auto emulators --instance <index> --capture
```

Discovery runs through the kernel and the `emulator_mumu` process plugin. The command creates a
traced run; `status`, `trace`, `inspect`, and offline HTML export work as for other run modes.
Blocked discovery runs may resume. No model call is involved.

## Discovery and selection

The adapter locates `MuMuManager.exe` beside running MuMu processes, then falls back to Windows
uninstall registry entries. Multiple detected installations require an explicit local
`GAME_AUTO_MUMU_MANAGER` override. No installation path is committed to configuration.

`MuMuManager.exe info -v all` supplies instance indices, startup flags, and ADB endpoints. Addresses
and ports are read from that response, including private bridged-network addresses. The adapter does
not guess a default port or scan the network. Public addresses and malformed endpoints are rejected.
The current adapter expects the indexed-object response format; unsupported formats fail explicitly
rather than being guessed.

`GAME_AUTO_ADB` overrides the ADB executable. Otherwise the adapter prefers ADB on PATH, then MuMu's
bundled executable. It never kills the shared ADB server or changes emulator debugging settings.
Each device query explicitly supplies the instance's serial.

Without `--instance`, all listed instances are reported. Stopped and starting instances are not
started automatically. A ready instance requires successful ADB connection, completed Android boot,
and confirmed version, ABI, screen size, and shell identity queries. One failed connection does not
hide other instances. A run is ready if at least one requested instance is ready; this does not
select an instance for future state-changing operations. Those operations must bind a specific
instance and device session.

`shell_uid` describes the current ADB shell identity. It does not establish whether root can be
enabled. Discovery and screenshots do not require root or attempt to grant it.

## Evidence and limits

`--capture` saves PNG files in a unique artifact directory under the run. The plugin returns image
metadata rather than binary data over RPC. The kernel checks size, PNG dimensions, and SHA-256
before recording the evidence reference. Screenshots stay local and are not uploaded automatically.

Commands have separate finite deadlines, persisted start/result records, and bounded output: 1 MiB
for ordinary stdout, 64 KiB for stderr, and 16 MiB for screenshots. Discovery has no aggregate
wall-clock deadline, so a list of instances is not constrained by a single command timeout.
Cancellation and trace-persistence failure stop the owned plugin process. The kernel's execution
lock prevents discovery from overlapping another local workflow executor.

This provides device discovery and observation, not application lifecycle or gameplay acceptance.
Any explicitly requested operator input observations remain local evidence, separate from the
framework's still-unavailable automated game-control capabilities.

`emulator.session` additionally resolves an explicit instance's identity, serial, and local ADB
executable for the independent Android plugin. It verifies creation identity, endpoint, connection,
and completed boot. Device requests pin the returned identity and endpoint; the adapter rechecks
them before each capability call. It does not reserve the instance or authorize mutations.
