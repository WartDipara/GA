# Source layout

Top-level directories separate runtime instructions, protocols, plugin processes and framework code.
They do not mirror every workflow stage with an empty package or placeholder plugin.

```text
config/                         Application settings and capability/Skill bindings
contracts/v1/                   Shared JSON schemas and input examples
docs/                           Architecture, commands and decisions
plugins/<capability>/           Independent stdio process implementations and manifests
skills/<skill>/                 Shared runtime AI instructions and reference material
tools/                          Maintained build and contract-generation tools
src/control_plane/              Strict TypeScript control plane during incremental migration
  contracts.ts                 Runtime validation against generated protocol types
  errors.ts                    Structured framework errors from the shared error-code authority
  protocol/                    Bounded JSON-RPC streams and child-process lifecycle
src/game_automation/
  __init__.py                   Package version metadata only
  __main__.py                   python -m game_automation entry
  cli.py                        Command-line parsing and dispatch
  common.py                     Shared identifiers, hashing, redaction and file primitives
  contracts.py                  JSON Schema loading and validation
  disk.py                       Disk space checks
  errors.py                     Framework error codes and default messages
  observability.py              Deterministic projection of recorded context and public events
  kernel/
    capability_router.py        Plugin registration and dependency routing
    plugin_host.py              Process lifecycle, call tracing and mutation authorization
    storage_policy.py           Storage budget configuration rules
    workflow/                   Stage orchestration and execution lifecycle
    gameplay/                   Planning, local routing, routines, progress and verification
  plugin_sdk/                   JSON-RPC, bounded commands, images and credential/privacy helpers
  prompting/                    Shared resource resolution and version-pinned AI inputs
  reporting/
    trace.py                    Offline execution/context report
    compact_trace.py            Bounded rendering for long traces
    gameplay.py                 Decisions, action timing and gameplay evidence
    task_index.py               Task-level execution and APK indexes
    game_comparison.py          Cross-game acceptance evidence comparison
    web/
      trace.template.html       Source template, distinct from generated trace.html reports
      dashboard.css
      dashboard.js
  storage/                      SQLite ownership, layout migration and task cleanup
var/                            Ignored local state, credentials, task artifacts and maintenance data
```

## Modules and package initializers

Use a module file for a single implementation rather than a directory containing only that
implementation and a forwarding initializer. Related multi-module areas use directories.

Python has supported implicit namespace packages since 3.3. Internal directories therefore do not
need empty `__init__.py` files in this Python 3.12 project. Imports name the defining module, for
example `game_automation.storage.store.Store` and `game_automation.kernel.workflow.runner.execute`.
There are no forwarding initializers that eagerly import a complete workflow or reporting stack.

The root `game_automation/__init__.py` remains because it exposes `__version__` and establishes the
regular root package. The wheel configuration explicitly includes `src/game_automation`, including
internal namespace directories and report assets. The console entry remains `game_automation.cli:main`.
`__main__.py` is the separate module entry point, not a package initializer.

See the [Python 3.12 import reference](https://docs.python.org/3.12/reference/import.html#namespace-packages)
and [PEP 420](https://peps.python.org/pep-0420/) for the language rules.

## Boundaries and generated data

Report rendering is separate from event collection and device authority. Source templates, CSS and
JavaScript stay together under `reporting/web/`; exported HTML lives under the selected execution's
`reports/` directory. Skills and contracts remain external shared dependencies, not copies in tasks.

Plugin directories remain independent process entry points. The kernel invokes their manifests;
other plugins must not import their internal implementations. Domain schemas retain their existing
v1 filenames, avoiding a directory-only protocol change. Configuration bindings remain under config/.

Code relocation changes pinned source hashes. Existing executions still need `--new-batch` after an
implementation update. This organization change does not rewrite task records, credentials or old
evidence. See [task workspaces](task-workspaces.md) and [cleanup](cleanup.md) for local runtime data.
The [runtime data layout](runtime-data.md) separates shared operational logs, inputs, caches and
temporary developer output from persistent task state.

TypeScript is checked with `npm run check`; `npm run build` also creates shared bundles under
`var/cache/typescript/`. `contracts/v1` remains authoritative. Generated types, installed npm
dependencies and bundles are not task-local copies. Python remains managed by uv.
