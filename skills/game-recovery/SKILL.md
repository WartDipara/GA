---
name: game-recovery
description: Recover the existing game session or the same bound identity.
---

# Game recovery

If a session-expired or other-device-login notice is visible, one normal reauthentication attempt
is allowed using the same bound identity. Dismiss the notice, follow the visible login flow and
reuse the credential reference if necessary. Do not clear application data, change accounts,
revoke external sessions or repeatedly fight another active session. A recurring conflict is a
blocker after at most two recovery rounds.

Declare recovery with a stable fault category: begin for a new recovery round, continue for its
subsequent steps and resolved only when current evidence resolves the fault. A new login step is
not a new recovery round. Kernel limits persist across batches. local_after screen values must be
actual perception screen_key hashes and control values must be supplied target_ref values. Semantic
descriptions belong in expected_outcome; leave local_after empty when no local predicate is known.

The kernel closes the target game when execution settles and may restore it on explicit resume.
Lifecycle records are separate from gameplay actions. A reopened game or changed process is not
proof that a pending purchase, reward, tap or navigation succeeded. Reconcile pending outcomes
from current business evidence and retained pre-action evidence; leave them unknown when that
evidence is insufficient. Do not replay an unresolved action after restoration.
