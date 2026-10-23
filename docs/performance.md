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
