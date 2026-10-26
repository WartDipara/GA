# Render exploration

`game-auto exploration <run_id> --format json` reads the task's discovered page and element catalog
without starting a model, writer or device. Pages form a directed graph, including self loops and
shared destinations. Elements retain separate container/item identities; coordinates are evidence,
not permanent identifiers. Canvas candidates come from the planner's current sanitized image.

The TypeScript kernel alone commits catalog records and their correlated delta events in one
SQLite transaction. Records are scoped by task, APK workspace, credential profile and observed
identity. Cleanup removes catalog records with their owning task. Checkpoints retain action links,
not copies of the complete graph. Old executions without a catalog return an empty projection.

Locators refer to retained observations instead of copying full images or Skills. Discovery creates
a candidate locator; only post-action verification promotes it. A deviated result invalidates that
locator while retaining its history. Dynamic appearance does not establish a new business state.

Rendered screenshots do not prove delivery to a translation hook or whole-game text coverage.

The trace toolbar links to `exploration.html`: a searchable, paginated element catalog with page
transitions, scroll coverage and per-element localization evidence. The standalone trace service
serves this view from current committed task records and polls every three seconds. Exported files
remain offline snapshots at the execution's business outcome, including blocked/failed outcomes;
later batches cannot silently change a rebuilt historical catalog. Report-only repair never calls
a model or device. Full JSON accompanies the bounded on-screen previews.

## Localization and scheduling

Memory separates semantic page/item identity, layout-bound locators and current observation bounds.
The local perception plugin matches unique accessible labels, or two coherent stable visual anchors
and one exact edge patch. Whole-page comparison ignores only recurrent non-target motion regions
learned over at least three inventory observations. Approximate, ambiguous, changed-layout and
unverified visual matches return to planning. Before local input, the executor checks the current
page fingerprint and exact target pixels again; a changed frame rejects the proposal before input.
Only verified outcomes promote a locator; deviations retire that locator version.

Current-page pending items precede navigation along a shortest verified safe path. Resource actions
still require planning and cannot be replayed merely because a balance changes. Known page return
paths may use local verification, with a fresh planner inventory after at most five local steps.
System Back is recorded as navigation, separately identified from screen tap targets.

Scrollable containers retain content fingerprints and per-container traversal state. Each swipe
exposes approximately 70 percent of the container with overlap. Bottom confirmation requires two
unchanged viewport results plus semantic bottom evidence. Repeated cycles, confirmed unbounded
content or 100 unique viewports seal that region as limited across resume and new batches.

External closure requires an originating tap, a newly observed focused window/task and the same
living game process. The runtime backs out only from that correlated window, then requires its
disappearance and the original game process in focus before recording external-skipped. Ambiguous
ownership cannot authorize closing an unrelated window. No browser force-stop or data reset occurs.

New pages/items, first verified operations and new scroll viewports advance the watchdog. Reopening
old nodes, animations and repeated navigation do not. Pending action links are checkpointed before
dispatch; catalog reconciliation finishes before clearing a pending action. The existing action
ledger remains authoritative, and unknown actions are never automatically replayed.

Verified elements carry action-run, binding-event, before/after observation and outcome-event
references. The writer verifies those references, dispatch ordering, retained artifacts and target
bounds before accepting verification or a traversable edge. Ledger outcomes, catalog verification
and pending-action clearance commit together. Scroll evidence is collected before that transaction.

Before planning with restored memory, the kernel checks historical verification. Missing or
conflicting proof downgrades the element to unknown, retires its active locators and invalidates
unsupported edges. Repair events and catalog changes commit together; original events and settled
business outcomes are retained. Valid legacy bindings acquire explicit proof without executing an
action. Reports show invalidated edges and link verified evidence to the action's original batch.

A blocked planner assessment settles the current execution after saving its observation, action
outcome and checkpoint. It records `exploration.branch.blocked` and preserves the remaining queue;
it does not report the branch as skipped or request another plan against the same evidence.
Rejected proposals and premature completion receive at most one correction round per observation.
Both attempts are traced before the watchdog blocks further planning. Resume obtains a new device
observation and reconciles pending actions before considering another input.
