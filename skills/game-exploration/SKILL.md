---
name: game-exploration
description:
  Select the next game testing action from current observations, available actions, and explored
  paths, with evidence, expected outcomes, and uncertainty.
---

# Interaction exploration

Prioritize unverified targets within the task scope. Reuse recorded paths and avoid repeated clicks
without new evidence. Associate each action with the current observation_id and session_id;
coordinates refer to the current screenshot dimensions. Return reason_summary, evidence_refs,
expected_outcome, and uncertainties in the supplied schema. If the image is unclear or a decision
cannot be supported, choose stop and state the missing information rather than guessing a click. Do
not interpret a suggested click, synthetic result, or disappearing button as business success. The
kernel enforces budgets and action restrictions; do not bypass them. Generate decision explanations
in English.
