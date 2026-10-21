# Plugin status

Implemented: agent_codex provides the pinned App Server handshake, explicit skill inputs, public
event collection, and structured results.

Implemented: task_intake provides canonical pass-through and text/Markdown extraction through the
optional ai.decide dependency, with kernel validation and clarification recovery. See
[task intake](../docs/task-intake.md).

Implemented: environment provides read-only ADB availability, deterministic device selection, boot
completion, and installed package checks. Commands and results are traced through the host. Device
verification remains pending. See [environment checks](../docs/environment.md).

| Planned module | Boundary                                                                   |
| -------------- | -------------------------------------------------------------------------- |
| android_device | Application lifecycle, observations, actions, post-action evidence         |
| perception     | Candidate elements and recognition evidence                                |
| exploration    | Path management, exploration policy, outcome verification                  |
| reporting      | Game coverage reports; the current exporter is an execution/context viewer |

Planned capabilities are intentionally not registered. Each implemented plugin has a manifest,
dependency declaration, and source. Plugins request declared dependencies through host.call and wait
for host.event persistence acknowledgements. No plugin writes SQLite. A separate process is not a
security sandbox.
