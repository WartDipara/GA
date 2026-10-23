# Performance and long-running execution

## Increment 1: progress protection

The gameplay watchdog persists active no-progress time, planning rounds, recent state/action
fingerprints and evidence-backed transition fingerprints in the existing checkpoint. Downtime is
not charged. Objective wording, exact frame animation and a model's progress boolean do not reset
the limits. A new transition requires a verified previous action citing the current observation;
previously credited transitions cannot repeatedly replenish the budget.

Defaults are 24 rounds or 900 active seconds without a new verified transition. Five identical
state/action proposals in the last sixteen actions block a cycle, including alternating paths.
The watchdog saves its evidence before blocking; unknown action outcomes remain pending. These
limits govern nonproductive execution, not the total task duration. Settings are pinned per batch.
A verified non-launch action resets the consecutive controlled-launch allowance, so a later
independent process exit is not charged to an old recovered failure.

Device and emulator calls have a finite aggregate deadline, defaulting to 120 seconds. Installation
uses its configured installation allowance plus 60 seconds for orchestration. Existing command
deadlines still apply. A timeout after dispatch remains unknown and is never automatically replayed.
Terminal reporting and report-only recovery remain independent of device availability.

Verification is Python compilation and maintained JavaScript syntax checks only. No runtime speedup
or new device acceptance is established by these checks.

## Increment 2: native observation and discovery

Native hierarchy capture writes to a unique device-side file, reads its actual XML and removes
the file. Raw XML is not stored in command logs. Candidate labels pass through credential
scrubbing. Available empty, malformed and unavailable trees have distinct trace events. The
standard UIAutomator command cannot establish a non-default display binding, so such displays
explicitly return unavailable and retain screenshot-based planning rather than using another
display's coordinates. Screenshots retain runtime consistency and privacy checks.

Session results may additionally return the resolved manager executable. The kernel binds that
path and the ADB path into subsequent session requests, avoiding repeated installation discovery.
Every session still checks the selected instance identity, endpoint and boot readiness. Existing
session fields remain valid; the new response field is optional in version 1 contracts.

## Increment 3: durable trace batches

The Codex adapter groups only records derived from the same received provider message. It awaits
one durable batch acknowledgement before reading the next message. This removes the extra
acknowledgement for the normalized event without buffering multiple provider messages or changing
their ordering. SQLite stores canonical payloads once; RPC envelopes reference and reconstruct
them through event IDs. Legacy individual event delivery is still supported. Rejected batches
retain diagnostic evidence, and failed persistence stops further work. Runtime-version and
session-setup phases are separately timed. Performance improvement requires later business evidence.

## Increment 4: diagnostics and observation reuse

Action results and their pending verification IDs are saved before requesting diagnostics. At most
one diagnostic collection runs per gameplay execution. Overlapping requests produce an explicit
skipped record instead of an unbounded queue. Collection errors retain failed call spans and an
unavailable event; authoritative persistence failure still stops execution. Finalization waits up
to five seconds, then cancels and joins the collector before report generation.

Game log windows retain boot, UID, PID and start-tick attribution. Normalized diagnostic records
remove overlapping lines and identify possible gaps; original bounded windows remain in call
outputs. These snapshots do not promise continuous logcat coverage. Boot changes invalidate data.

Perception results are reused only for the same observation/comparison pair, with a source event
reference. The cache holds at most four entries. Guarded routines can consume already collected
facts rather than immediately settling the same starting point again. Every dispatched action
still performs the existing fresh process, display and target-image safety checks.

## Increment 5: incremental evidence budgets

SQLite maintains serialized event bytes with transactional insert, update and delete triggers.
Existing events are backfilled once in an atomic initialization under the execution lock. Read-only
views remain compatible with databases that predate this counter. Run deletion cascades to its
counter, so explicit task cleanup still removes all owned records.

The storage owner counts execution files once at admission, then tracks known artifact writes,
planner images, credential envelopes and plugin output workspaces. Each plugin completion or
failure accounts for files in its output directory. Full reconciliation runs every five minutes
and on process restart. Logical event bytes remain separate from actual filesystem free space;
every admission still checks free space, the evidence budget and reserved observation/report
headroom. Unclean shutdown cannot leave a trusted stale file counter. No evidence is deleted to
make the counter fit its budget.

## Follow-up: lifecycle and long-trace completeness

Provider completion is acknowledged only after the durable event batch and must match both the
active thread and turn. Semantic call-chain projection traverses all relevant committed events
up to a captured sequence watermark, instead of taking only the latest 20,000. Starts and ends
use the same boundary. Detailed provider stream records remain in SQLite; bounded UI previews and
the emergency compact renderer are explicitly separate from the complete semantic call chain.
