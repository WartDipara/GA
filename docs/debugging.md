# Debugging a run

List recent runs before opening a specific trace:

```powershell
uv run game-auto runs --limit 20
```

Each entry includes its ID, task subject, mode, status, stage, and creation time. Both historical
UUID IDs and new readable IDs appear in the list.

APK batches use `run_<count>-apks_<execution_id>`, such as
`run_3-apks_6e57a22c87834f148609dc70dc91ab26`. The suffix is a fresh full UUID4 in 32-character
hexadecimal form for each execution. It is independent of the submission task_id, so repeated runs
of one task receive different directories. Other run types use their target package or document
filename stem as the subject. There is no timestamp, mode prefix, custom name, or sequence suffix.

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

1. Read run.state for the stage, status, and blocker.
2. Find call.failed/call.incomplete and inspect its span, inputs, attempts, and output.
3. For AI calls, inspect provider.wire for actual turn/start inputs, explicit skill paths,
   outputSchema, and provider errors or turn/completed.
4. Browse public reasoning summaries when supplied. Missing summaries do not expose hidden
   reasoning; decision explanations and evidence remain inspectable.
5. Compare decision.proposed, action.intent, action.result, and verification.\*. Suggestions,
   execution, and verified effects are separate facts.
6. Inspect the run's manifest.json, skills/, calls/, artifacts/, reports/trace.html, and
   reports/context.json.

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

The HTML's embedded local script only browses saved data; CSP blocks external code, network access,
and form submission. Opening or exporting it never starts a plugin, invokes AI, or operates a
device.
