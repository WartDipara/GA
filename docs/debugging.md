# Debugging a run

List recent runs before opening a specific trace:

```powershell
uv run game-auto runs --limit 20
```

Each entry includes its ID, task subject, mode, status, stage, and creation time. Both historical
UUID IDs and new readable IDs appear in the list.

Use `game-auto tasks` to locate a business task, then `game-auto task-status <task_id>` to rebuild
its stage index. Task folders live under `var/tasks/<task_id>/`; every execution is a child under
`executions/<run_id>/`. New task IDs contain the initial APK count, target or document stem plus a
full UUID4. New execution IDs use `run_<uuid4>`. Historical IDs stay unchanged after migration.
There is no timestamp or sequence suffix. Different tasks with the same APK remain separate.

Allocation checks existing database IDs under an immediate write transaction and creates the
directory exclusively before recording the run. A collision with a database entry or existing
directory generates another UUID, up to 10 attempts; exhaustion fails without reusing an existing
directory. The database primary key also enforces uniqueness. Run IDs and directories remain fixed
after creation, including after APK inspection or resume. Existing directories are not renamed, and
snapshot compatibility checks still apply.

```powershell
uv run game-auto status <run_id>
uv run game-auto context <run_id>
uv run game-auto context <run_id> --json
uv run game-auto trace <run_id> --follow
uv run game-auto inspect <run_id> --span <span_id>
uv run game-auto export-trace <run_id> --format html
```

For a periodically refreshed task view, run this in a separate terminal while work continues:

```powershell
uv run game-auto serve-trace <run_id>
uv run game-auto serve-trace <run_id> --interval 5 --port 0
```

Open the printed loopback URL. The default port is 8767 and the refresh interval is three seconds;
port zero selects an available port. The viewer follows the run's entire business task, including
child stage executions created later. It shows execution states and the latest 200 durable events
across the task, with recorded durations and bounded payload previews. Search and execution filters
apply to that bounded window; open a stage trace for earlier evidence and full model context.

The page provides pause/resume, manual refresh and interval selection. Polls do not overlap. A
revision check avoids transferring the same events again, and the selected event and filters survive
updates. Failed refreshes preserve the previous snapshot and show a retry notice. Removing the task
or stopping the server does not make the retained browser snapshot current evidence.

The service uses independent SQLite connections with `mode=ro` and `query_only`, and a consistent
read transaction per request. It neither holds the workflow execution lock nor migrates the database
or writes report files. It binds only to `127.0.0.1`, exposes no action endpoints or directory listing,
and serves only the selected task's trace views and allowed report/evidence types. Credential and
manifest files are not served. Detailed trace links open a current snapshot in another tab; the main
page continues polling. Ctrl+C stops only the viewer, leaving the workflow untouched.

This viewer increment has compilation and JavaScript syntax verification only. Browser refresh and
concurrent workflow behavior still need acceptance during an explicitly requested real task.

The live workspace uses an execution navigation tree, a searchable event list and a persistent event
inspector. Status segments count executions, including workflow parents and their children, rather
than APK coverage. The duration chart ranks at most five timed module-call completions from the
latest 200-event task window; it is not an all-history ranking or a sum of nested execution times.
Click a bar to inspect its event. Missing timings stay unavailable. A selected event retained after
leaving the recent window is explicitly marked as pinned. Dark and light themes are available.

Design references are [Langfuse's nested observations and trace details](
https://langfuse.com/docs/observability/overview) and [Jaeger's trace UI and theme controls](
https://www.jaegertracing.io/docs/latest/frontend-ui/). The implementation uses local CSS charts and
the existing SQLite projection; it does not embed either product or add a visualization service.

1. Read run.state for the stage, status, and blocker.
2. Find call.failed/call.incomplete and inspect its span, inputs, attempts, and output.
3. For AI calls, inspect provider.wire for actual turn/start inputs, explicit skill paths,
   outputSchema, and provider errors or turn/completed.
4. Browse public reasoning summaries when supplied. Missing summaries do not expose hidden
   reasoning; decision explanations and evidence remain inspectable.
5. Compare decision.proposed, action.intent, action.result, and verification.\*. Suggestions,
   execution, and verified effects are separate facts.
6. Inspect the run's manifest.json, referenced shared resource roots, calls/, artifacts/,
   reports/trace.html, and reports/context.json. Resource hashes must match; per-execution Skills
   and contracts directories exist only in legacy snapshots.

The context dashboard groups observed instructions, skills, inputs, schemas, responses, summaries,
tools, and attachment references. Text estimates are approximate. Reported usage is separate;
repeated cumulative updates are not billed repeatedly. Unknown baselines remain unknown.

Supplements are append-only task.supplement events. Authentication/quota conditions become
AI_UNAVAILABLE; resolve the condition and resume. CODEX_VERSION_MISMATCH requires the pinned
version. SNAPSHOT_CHANGED/RUNTIME_CHANGED require a new execution batch. ACTION_ALREADY_DISPATCHED
requires reconciliation, not a force replay.

Device preparation reports `DEVICE_INSTALL_UNKNOWN` when a dispatched installation cannot be
verified. Resume performs package inspection without sending another installation. Check the
installation span's action.intent, action.dispatched, action.result, and device.package.observed
events. `prepared` actions lack dispatch authorization; `executed` actions still need fingerprint
verification. A different APK or instance cannot bypass a reservation. If a runtime upgrade requires
a new execution batch, submit the same source workspace and instance again; it consults prior
installation records. See [device preparation](device-preparation.md).

## Record boundaries

Records contain public interface traffic, not all provider-internal context or complete hidden
reasoning. Raw and normalized records are redacted by rules; arbitrary prose and screenshots may
still contain sensitive content. Runtime materials stay local and are not uploaded automatically.
Use credential references rather than raw secrets.

Process or disk failures may leave an action without a result. unknown/incomplete is missing
evidence, not a result to fill in. Failed trace persistence prevents new actions even when a final
error cannot be saved.

Exported HTML only browses saved data; its CSP blocks external code, network access and form
submission. The local live viewer additionally permits same-origin polling. Neither view starts a
plugin, invokes AI or operates a device.
