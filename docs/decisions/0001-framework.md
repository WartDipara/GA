# ADR 0001: Kernel, plugins, skills, and evidence

Status: accepted. Date: 2026-10-21.

The objective is a modular Android testing workflow with inspectable failures. Use Python 3.12,
independent JSON-RPC process plugins, and SQLite. This is a local single-executor system without
distributed scheduling. JSON Schema allows capabilities to be replaced by implementations in other
languages.

Choose Codex App Server stdio for explicit skill inputs, continuous events, and session restoration.
Pin 0.162.1. Keep provider protocol and strict-output adaptation within agent_codex. Skills guide
decisions; code enforces actions and evidence. Runtime skills remain separate from development
AGENTS.md.

Tracing is an execution prerequisite: event persistence must be acknowledged, and action intents
precede dispatch. Unknown device outcomes must not be replayed. SQLite is the authoritative
event/state source; browser/JSON reports are rebuildable projections.

Task intake is implemented. Future modules are Android control, perception, exploration, and game
coverage reporting. They must not be registered as working placeholders.

References: [App Server](https://learn.chatgpt.com/docs/app-server),
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference).
