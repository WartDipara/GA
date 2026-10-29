# Exploration supervision

The TypeScript kernel owns progress decisions and recovery budgets. Model state names,
coordinates and discovery counts do not independently reset the exploration watchdog.
A first verified element requires the existing action ledger and post-action observation chain.
Both local navigation and planner-driven actions feed the same supervisor after settlement.

The supervisor retains 48 transition fingerprints across resumes. Fingerprints use page,
element, action type and destination, not pixel coordinates. Three repeated transitions
without a first verification open a recovery round. The configurable
`gameplay.supervisor_recovery_rounds` defaults to two. Recovery counts belong to persistent
element records and survive batch changes. Model fault labels do not reset these counts.
After recovery exhaustion the element becomes limited and is excluded from route selection.

`exploration.supervision.progress` records accepted and rejected progress with action and
observation references. `exploration.supervision.intervention` records recovery rounds,
limits, quarantine and the instruction supplied through the persisted planner context.
These records use the existing writer and transaction; no extra log writer or model is used.

The bounded transition window is not proof that every long cycle can be recognized.
The existing no-progress round/time limits remain a final backstop. Recognition and semantic
outcomes still depend on current evidence; compilation does not demonstrate gameplay coverage.

## Isolation and stopping

Recovery feedback suspends the offending target from local selection and verified navigation
routes. Planner proposals targeting it are rejected before dispatch, including long presses.
The final device admission also checks suspension to cover a watchdog intervention that occurs
after planning. Fresh observations do not reset planning-correction counts for the same page
and reason. Kernel-issued correction counts are persisted alongside the watchdog checkpoint.

At the no-progress boundary, a settled, identified target can enter bounded recovery and the
kernel can redirect to another reachable frontier. This is recorded as a redirect, never as
verified progress. Unknown pending outcomes cannot be bypassed. Exhausted recovery leaves the
target limited across batches. If no alternative is reachable, or the planner repeatedly ignores
alternatives, the execution becomes blocked and uses the existing settled-state report pipeline.

Scroll progress requires a verified action, stable post-action observation, changed local
viewport, a matching non-loading scroll review and a new semantic item inventory. Pixel changes
alone do not reset the watchdog. At most 100 semantic inventories are retained per region.
Inventories without identifiable items may remain unknown; text-only regions are not claimed
complete from changed pixels. Model recognition errors remain possible.

Supervision state and events are committed through the existing writer transactions. Interventions
reference their recent progress event IDs. Full events remain in the trace; the execution outcome
shows the latest 50 interventions. The exploration report exposes persistent target recovery counts
and isolation reasons, including through its historical state projection. No AI generates these
records or reports. Existing credential references and redaction remain unchanged; no additional
content-scanning or inference pass is introduced for logging.

This increment does not introduce an independent process-health monitor. Existing command/model
timeouts remain active. Forced process loss or unavailable storage can delay publication; report-only
recovery uses committed evidence and never replays device input. Real-game acceptance is pending.
