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
