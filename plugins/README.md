# Plugin status

Implemented: agent_codex provides the pinned App Server handshake, explicit skill inputs, public event collection, and structured results.

The synthetic plugin is in tests/fixtures/plugin. Every capability uses the fixture. prefix.

| Planned module | Boundary |
|---|---|
| task_intake | Natural-language/documents to canonical tasks, source attribution, clarification |
| environment | Device, package, permission, and required-environment checks |
| android_device | Application lifecycle, observations, actions, post-action evidence |
| perception | Candidate elements and recognition evidence |
| exploration | Path management, exploration policy, outcome verification |
| reporting | Game coverage reports; the current exporter is an execution/context viewer |

Planned capabilities are intentionally not registered. Each implemented plugin has a manifest, dependency declaration, source, and integration tests. Plugins request declared dependencies through host.call and wait for host.event persistence acknowledgements. No plugin writes SQLite. A separate process is not a security sandbox.
