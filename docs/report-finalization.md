# Report finalization and recovery

Each settled execution automatically publishes `reports/index.json` under the execution-index
contract, `reports/index.html`, shared report navigation and task/history indexes. This includes
blocked checkpoints, failures and cancellations. Report-only recovery uses the same pipeline.
See [Trace workspace](trace-workspace.md) for the independent viewer and storage layout.

SQLite events and action records are authoritative. HTML and JSON reports are recoverable
projections. A renderer failure must not convert a completed game operation into a business failure,
cause the scheduler to repeat it, or prevent another report component from being attempted.

TypeScript owns publication order, component boundaries, receipts, pending-request repair and the
minimal outcome report. Python rich renderers are read-only projection tools: they may write report
files, but cannot publish states or events. Their temporary compatibility bridge is separate from
the gameplay loop. If that bridge disconnects, the kernel still writes the minimal outcome and a
degraded receipt. Report components have spans and measured durations; interrupted publications
close unfinished components as incomplete before a new publication attempt.

## Publication order

1. Commit the business outcome and `report.requested` in the same SQLite transaction.
2. Enter the runner's `finally` path after success, failure, cancellation or blocking.
3. Generate the minimal execution outcome first, followed independently by mode-specific reports,
   the Agent trace, context details and the task index.
4. Atomically publish `reports/publication.json`, then append `report.finished` in SQLite.

The optional workflow report stage requests publication after the business outcome. It no longer
renders an index while its owning workflow is still running. Finalization is mandatory even when
the configured stages omit that report stage or an earlier stage fails.

Snapshots contain the business status, last state-event ID, observed event sequence and generation
time. `final` means succeeded, failed or cancelled; `checkpoint` means blocked and potentially
resumable; `in_progress` means a live or manually exported intermediate projection. A final failed
report does not establish task completion or game coverage. Live views remain current projections,
not proof that the workflow has finished.

## Independent outcomes

`report.component.started/finished` retain each component's measured duration and errors. Report
requests, attempts and failures are append-only. Publication status is independent of run status:

| Publication state | Meaning |
| --- | --- |
| pending | The outcome committed, but its request has no finished publication |
| complete | All components completed and declared output files are present |
| degraded | A component or publication receipt failed; other outputs may be available |
| missing_files / changed_files | Declared outputs disappeared or changed size |
| unavailable | Output availability could not be checked |
| untracked | Historical execution predates publication tracking |
| in_progress | The business execution has not reached a settled state |

The status command and live task navigation expose publication state separately. A completed
business execution still returns its business exit status, with a warning if publication is
incomplete. An explicit report-repair command returns a nonzero exit code for degraded publication.
This is not permission to ignore critical persistence failures: failed SQLite writes still escape
and prevent further device actions.

## Partial evidence and file safety

The minimal `execution.json/html` report requires no screenshot, action input file, Skill snapshot,
pricing file or rich frontend asset. It records the actual outcome, original reason, recent failed
calls and unresolved actions. Missing retained action inputs are marked unavailable while other
action records remain usable. Missing or invalid optional version/pricing evidence stays unknown.
A failed rich trace renderer falls back to a bounded recorded-event timeline with a degradation
notice. The original event store and retained files remain available for diagnosis.

Report writes use a unique temporary file in the destination directory, UTF-8/LF encoding, flush,
file `fsync`, then atomic replacement. An unsuccessful write does not truncate the previous target.
Different files are not a single filesystem transaction; `publication.json` and the SQLite finished
event identify whether the generation completed. Files from an older generation may remain when a
component fails, so their existence alone is not proof of current publication. Availability checks
detect missing files and changed sizes, not arbitrary same-size corruption.

## Restart and report-only repair

```powershell
uv run game-auto report <run_id>
uv run game-auto report --pending
uv run game-auto status <run_id>
```

These commands never call a model, launch a game or dispatch an Android operation. They require the
execution lock, so they cannot label an active framework execution as interrupted. Queued work is
not treated as a finished task.

Before business execution resumes, the same lock owner reconciles leftover running executions.
Unclosed call, step and workflow-stage boundaries receive `incomplete` events with unknown duration;
the run becomes blocked with `PREVIOUS_PROCESS_EXITED`. Device action outcomes are not changed to
success. Fresh device reconciliation is still required before future gameplay can continue.

Pending/degraded publications are retried once per entry before business work, independently of
runtime manifest compatibility. Old Skills or changed code therefore do not prevent report-only
reconstruction. Terminal business runs are never restarted by repair. Historical executions without
publication tracking are rebuilt only when explicitly selected. Successful component timing records
are retained even if a later renderer fails.

| Failure boundary | Recovery behavior |
| --- | --- |
| Game/plugin failure or deadline | Persist actual failed/blocked outcome, then finalize all reports |
| Cancellation before dispatch | The same `finally` path publishes outcome and other reports |
| Snapshot preparation failure | Persist blocked/cancelled state and publish available evidence |
| Renderer exception or missing attachment | Record component failure or evidence gap; attempt remaining components |
| Process dies after outcome commit | The pending request is retried without executing the task |
| Process dies during active work | On the next locked entry, mark unclosed boundaries unknown and publish a checkpoint |
| Process dies after receipt write but before finished event | Retry the publication; never infer completion from file presence |
| Disk is full/unwritable or SQLite cannot commit | Preserve what was already durable; repair after storage is restored |

No process can generate a file after it has been forcibly killed, or write a report to a failed or
full disk. Recovery needs the retained SQLite database and usable storage. Missing screenshots
cannot be recreated from a report, and database/device uncertainty must remain explicit. Cosmetic
reporting failures may degrade; uncertain device actions and critical trace failures must block.

Development verification remains compilation and JavaScript syntax checks only. Rebuilding reports
from existing successful/blocked business evidence does not validate hard-kill, disk-full or power
loss behavior. Those failure paths have been reviewed in source; no mock, smoke, synthetic inference
or fault-injection runs are used for this increment.
