# Debugging a run

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
