---
name: game-exploration
description: Inventory reachable viewports and select the next scoped target.
---

# Game exploration

For explore, every proposed tap/swipe target must also appear in this turn's exploration.elements.
When a reveal or modal covers a panel, inventory the topmost modal as its own page/variant and
exclude obscured underlying controls. Include a visible dismissal/continue region when proposing
to tap it; do not propose it while omitting it from the inventory. For a confirmed informational,
nonfinancial modal, an evidence-based Android Back proposal can be considered once. Verify the
effect before any subsequent action; a changed reward reveal can mean another reveal, not closure.
last_rejected_proposal.dispatched false means the proposal did not reach the device. Correct its
inventory/preconditions rather than describing that proposal as an uncertain executed action.
For explore, build a small dependency-ordered subgoal queue from visible evidence. Complete necessary
tutorial or main-story prerequisites before locked functions. Record progress_update only from
current evidence, distinguishing discovered, attempted, verified, blocked and unknown. Existing
milestones describe prior observations, not authoritative current server state. On resume, compare
the current screen and identity with the restored checkpoint before continuing. Prefer unverified
reachable entries over repeating a verified path. Reuse normal cached login when still valid.

For explore, include exploration on every planning turn. Before authenticated gameplay, set
authenticated false. After entry, enumerate all currently visible interaction candidates and
scrollable containers, including canvas icons missing from the Android control tree. Use short,
stable English page_key, variant, layout and element keys. Ignore animated backgrounds, cooldown
ticks and changing resource counters when naming the page. Preserve meaningful item names and
numbers. Identify repeated controls by container plus item, not by label or coordinates alone.
Use image coordinates for every inventory rectangle. Anchors must be stable visible landmarks;
do not choose countdowns, particles, moving characters or transient badges. Record unknown or
ambiguous targets instead of guessing. inventory_complete describes this visible viewport only.
Memory is historical evidence. Re-locate each target in the current image before proposing input.

Exploration is for rendering reachable content and observing stability. After password login and
small-account selection, continue through actual game entry directly into exploration. Do not stop
after one panel. There is no translation-hook acknowledgement in this task: do not claim text was
reported. Do not read or summarize long passages; expose every viewport and open distinct content.

Use at least two distinct stable anchors when available. Keep page_key and element keys identical
to existing memory when evidence identifies the same page or item. A resource count, countdown,
particle effect or badge does not create a new page. Distinguish a layout variant from a different
content item. Preserve numbers that identify stages/items. A newly unlocked control is new content.
Set viewport_key to a short stable identifier for the current visible content where useful.
Reuse known_pages.page_key and variant exactly when current evidence identifies a retained page,
including after a new model session or execution batch. The opaque page ID is not a page_key.

Historical elements with verification_issue or missing verification proof do not establish a
successful interaction. Reconcile their labels and identities against the current image; do not
repeat an old resource action to repair a missing proof or treat historical descriptions as facts.

Follow exploration_memory.recommendation when it is supported by current evidence. Process pending
items on the current page before leaving. Use known safe transitions to reach other pending pages.
Do not keep reopening verified entries or repeating rewards, summons, upgrades or toggles merely
because they remain clickable. Inventory resource operations once per content item; resource count
changes alone do not reopen them. In-game resource consumption is allowed, but no real payment,
external recharge, account deletion or account trading. Record locked level/story/combat entries
and continue other reachable content; only necessary introductory guidance is in this scope.

Inventory scroll containers with kind scroll and intent scroll. Swipe upward within the container
to reveal lower content, about 70 percent of its height with overlap. Scan each resulting viewport
for new targets. In scroll_review, use the container's original semantic key and report at_end only
from actual bottom evidence; loading or an ineffective gesture is not a bottom. The kernel requires
two unchanged swipe results as well. Cyclic/unbounded or limited regions remain sealed across
resume: do not reenter, reopen or retry them, and continue other entries instead.

Treat a newly opened external browser/page as external, preserve the game process and do not
explore its content. The kernel correlates the originating action and closes only that new window.
An embedded game page is still game content; do not classify an Activity/display change alone as
external. If closure or process identity is unclear, retain uncertainty.

Complete only when the discovered frontier is settled, all reached viewports are scanned and the
root has been rechecked for new entries. Include locked/prohibited/limited/unknown gaps; do not
claim all-game coverage. Do not mark a parent complete by recursively waiting for all descendants:
the page graph can contain cycles and shared destinations.

Use the catalog for element coverage; keep subgoals limited to actual entry prerequisites,
not one subgoal or milestone per button. A locked/prohibited branch is a recorded gap, not a reason
to stop the whole task while other pending entries remain. If the kernel rejects an already settled
target, select the recommended pending target or explain a missing return path instead of repeating
the same proposal. Resource count fluctuations alone never reopen a settled operation.

Kernel exploration supervision is authoritative. Its feedback identifies repeated transitions,
remaining recovery rounds and quarantined elements. Choose a different evidence-based route or
another pending target after feedback. Renaming a state or fault does not reset recovery counts.
Never retry quarantined elements. New animations and renamed screens are not verified progress.
Treat tutorial overlays as temporary interruptions; later unlocks may introduce another tutorial.
