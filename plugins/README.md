# Capability plugins

Each enabled directory owns an independent JSON-RPC 2.0 / stdio process, a `plugin.toml` manifest
and its implementation. The kernel loads bindings from `config/plugins.toml`; names do not imply
that another plugin may import private implementation files.

| Plugin | Implemented boundary |
| --- | --- |
| agent_codex | TypeScript App Server adapter, explicit Skills, structured decisions and public events |
| task_intake | Canonical JSON handling and AI-assisted text/Markdown intake |
| environment | Read-only ADB availability, boot and package checks |
| apk_preprocessing | Resumable downloads, archive checks and concise AAPT2 metadata |
| emulator_mumu | Vendor discovery, verified sessions and kernel-authorized application activation |
| android_device | Package installation, process/display state, capture, guarded input and scoped logs |
| perception | Local frame comparison and accessible-control candidates; unknown canvas semantics |
| gameplay_tools | Image resizing and encrypted credential preparation, without state or device authority |

Plugins request declared dependencies through `host.call` and await event-persistence
acknowledgements. They never access the business database. stdout is protocol-only. A separate
process is not a security sandbox.

Build TypeScript plugins with `npm ci` and `npm run build`. Their manifests use the pinned Node
runtime and shared bundles under `var/cache/typescript`. Execution admission checks build-source
hashes before accepting a bundle. Runtime manifests pin TypeScript sources, the dependency lock,
the bundle and Node identity. Missing or stale builds block admission instead of silently using a
previous artifact. Windows uses the native pinned Codex executable without shell interpolation.

The control plane's `apps/control-plane/src/gameplay/` coordinates exploration, routines and battle goals. Offline report
generation lives under `src/game_automation/reporting/`; there is no placeholder reporting plugin.
Device and gameplay acceptance remain bounded by recorded evidence, separately from code availability.
See [gameplay](../docs/gameplay.md), [device preparation](../docs/device-preparation.md) and
[source layout](../docs/project-layout.md).
