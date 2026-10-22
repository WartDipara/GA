# Composed APK workflow

The `workflow` CLI owns one business task and coordinates existing execution modules. It downloads
and inspects the complete APK list, then runs each APK's device and gameplay stages in input order.
Five download workers remain independent of the single active gameplay execution.

```powershell
uv run game-auto workflow --input var/inputs/apks.json --instances 0 1
uv run game-auto status <workflow_run_id>
uv run game-auto trace <workflow_run_id> --follow
uv run game-auto resume <workflow_run_id> --input var/inputs/apks.json
uv run game-auto cancel <workflow_run_id>
```

The example requires two APK URLs and two distinct, already running MuMu instances with indices
0 and 1, assigned in URL order. Instance indices identify MuMu instances; they are not display IDs.
The scheduler does not create, boot, reset or recycle emulator instances. Existing device bindings
still enforce workspace isolation. Use a dedicated instance for each APK, including duplicate URLs.
An instance already bound to a different APK workspace is rejected before installation.

The default goal is `login-and-interact`, using the encrypted local `default` credential profile.
Override it with `--credentials <profile>` and `--goal explore` or `--goal advance-battle`.
`--max-turns <count>` sets an optional per-invocation planning boundary; zero leaves it unlimited.
There is no new overall workflow deadline. `--adopt-existing` requires the installed APK fingerprint
to match, and preserves application data. Default preparation follows the existing guarded install
policy. Completing the selected goal does not establish whole-game coverage or battle settlement.

## Composition

`kernel/workflow/assembly.py` is the composition root. Each `Stage` declares its execution mode,
input adapter, required predecessor outputs, scope and capabilities. `WorkflowBuilder` validates the
ordered stages before any stage executes. The scheduler does not dispatch on business stage names.
It passes execution IDs between adapters, leaving installation, actions, planning and verification
inside their existing kernel modules.

The default composition is stored in `config/application.toml`:

```toml
[workflow]
stages = ["preprocess", "environment", "prepare", "launch", "play", "report"]
```

For a shorter execution, override the selection without editing modules:

```powershell
uv run game-auto workflow --input var/inputs/apks.json --stages preprocess report
uv run game-auto workflow --input var/inputs/apks.json --instances 0 --stages preprocess environment prepare report
```

The second example requires one APK. Download-only composition needs neither instances nor a
credential profile. Removing a prerequisite while retaining its consumer is rejected. APK-scoped
stages form one contiguous group so that each APK finishes that group before the next APK starts.
Batch stages may follow the group, but cannot consume an individual APK's output as a batch result.
To connect another existing module, register its `Stage` and input adapter in `build_workflow` and
include its name in the composition. No scheduler loop or plugin internals need to change.

## Checkpoints and evidence

The scheduler holds the existing process execution lock across the entire workflow. Child stages
use the same lock ownership rather than acquiring it again. Each child inherits the parent's pinned
manifest and shared resource references; this does not copy Skills or contracts. Child runs remain
inside the same task's `executions/` directory. There are no new business tables or log files.

SQLite `workflow.stage.*` events record stage starts, child execution bindings, completions,
interruptions and failures. Each attempt has its own span and monotonic `duration_ms`. An interrupted
attempt has unknown duration. Recovering a child's already committed success does not invent a
duration for its earlier execution. Detailed action and model timings remain in the child trace.
`status` projects current child states; `reports/workflow.json` saves the workflow checkpoint view.
The workflow HTML trace links to child traces, and the task report indexes all execution reports.
Offline status and export commands never invoke devices or models.

Use `game-auto serve-trace <workflow_run_id>` in another terminal to poll the whole task every three
seconds. The local page shows all stage states and the latest 200 task events, including durations,
and links to detailed stage traces. Refresh, pause and interval controls are built in. See
[debugging](debugging.md) for read-only boundaries and custom ports.

Resume the owning workflow, not its child execution. Successfully checkpointed stages are skipped;
an incomplete or blocked child resumes using its original run and action ledger. Even a child
created just before a crash is rediscovered before another child can be created for that step.
Existing device reconciliation rules inspect unknown outcomes before taking further actions. A
blocked, failed or cancelled child stops progression to the next APK. A terminal child is not
automatically replaced or retried. Cancelling the workflow reaches the active child's host checks
and prevents later stages from starting.

Keep the original URL file available when downloads require recovery; signed download URLs are
not stored in the workflow submission. Changing code or shared dependencies requires a new batch.
`resume --new-batch` creates a fresh workflow execution using the existing submitted composition.
It links successful child executions from the preceding batch, preserving APK workspaces and
instance bindings. Unfinished stages get new executions under the current pinned implementation.
Device stages still reconcile earlier actions against fresh observations; a new batch never grants
permission to replay an unknown action. Only identical task inputs and stage modes can continue this
way. Historical failures remain intact. To change composition, submit a new workflow instead.

The scheduler has been compiled, but batch installation and sequential gameplay require a separate,
explicit real task with the necessary instances. Compilation does not prove device behavior.
