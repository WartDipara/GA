# Hybrid gameplay workflow

Development follows independent increments. A working device adapter does not establish game
coverage. Every proposed action must pass kernel authorization and produce subsequent evidence.

## Runtime foundation

Use `game-auto prepare-device --from-run <run> --apk-index <index> --instance <index>` to prepare
an APK. Add `--adopt-existing` to bind an already installed APK only when its complete fingerprint
matches. Adoption preserves application data and cached login; it never records a fictitious install.

Use `game-auto credentials default` to enter the username and password at hidden prompts. Only a
current-user Windows DPAPI ciphertext is stored under the ignored `var/secrets` directory. The
runtime passes a profile reference, never plaintext, over RPC. Secure input travels over the owned
ADB process's stdin; command output is suppressed. Known credentials are removed from text and
accessible password controls are masked before image persistence. Additional regions must be
identified before typing into a custom-rendered input field.

`game-auto play --prepared-run <run> --goal capture` binds the same dedicated instance, verifies
the installed fingerprint, captures process/task/display evidence and package-process logcat, and
saves a checkpoint. `--goal return-to-game` authorizes a foreground return only while the target
process is alive, then verifies the focused package using new evidence.

Action intents are durable before dispatch. A transport error after dispatch leaves an unknown
outcome. Observation, not retry, is the first recovery operation. The adapter compares boot ID,
process start ticks, focused display, rotation and a fresh target-region image before input.

Logcat windows are bounded, identify their source process lifetime, and cover exact target-package
process names. Isolated WebView renderers and system crashes are not silently attributed to the
game. Empty windows are valid; unavailable windows are explicit. Existing tracing records all calls,
inputs, results and verification events; there is no separate business-log implementation.

Evidence admission reserves 4 GiB for the batch and 2 GiB of free disk space. Admission repeats at
each capture/action boundary with headroom for bounded evidence and report generation. Existing
evidence is not removed to continue a task. Compilation is development verification only; actual
game task evidence is local and must be reported separately.

## Progress and exploration

`--goal explore` uses the same hybrid loop. The planner may propose prerequisite-ordered subgoals
and concise milestones; the kernel validates references and dependency cycles before committing
them. The latest 50 path edges are context, while complete action history remains in SQLite events.
Identity, APK fingerprint, dedicated instance and credential revision scope the checkpoint. A vault
profile replaced in place cannot silently reuse another account's progress.

Recovery declarations use stable fault categories and a persisted two-round limit. A new batch
observes the device before resolving pending actions or planning. Unknown clicks are never replayed.
Real device evidence confirmed normal cached reauthentication, selection of the same small account
after its list position changed, and restoration of the bound role in gameplay. The later model
connection interruption retained the pending action and checkpoint for semantic reconciliation.

## Local scheduling and routines

`perception.analyze` produces accessible candidates, a small local image signature and measured
frame difference. A canvas without accessible controls remains unknown. Stability only indicates
limited visual change; it does not establish loading completion or business progress.

`game-auto play --prepared-run <run> --goal routine --routine <file.json>` accepts the
`gameplay-routine` contract. Each of at most five steps requires a current screen signature plus
explicit conditions. The routine executor proposes one action, the kernel authorizes it, and the
outcome verifier checks new observations before advancing. A modal, rotation or changed condition
returns control for planning. Predicates such as focused package prove that predicate only, never
successful login or a completed level. A missing result remains unknown and cannot be replayed.

The decision router has no model calls. Local recognition, routing, routine execution and outcome
verification are independent components; the device adapter has none of their business rules.

## Planning and real-task acceptance

`--goal login-and-interact` invokes the hybrid workflow. `--max-turns <n>` provides an optional
business boundary for one invocation; the default is unlimited. Ordinary transition waiting uses
local observations. Each semantic turn reviews a pending action and proposes the next step.
The prompt contains frozen base/stage Skills, the actual context, and sanitized `localImage` items.
The model cannot execute tools. Unknown outcomes block new input; observed deviations can be
reconciled without claiming the original expectation succeeded.

Codex 0.163.0 was reviewed using its locally generated App Server schemas for `localImage`, explicit
Skill inputs, structured output and thread restoration. No synthetic model verification was used.
Local Windows OCR masks known credential text in canvas-rendered screens before persistence. OCR
is a best-effort text detector, not a guarantee for arbitrary private content; declared sensitive
fields are masked geometrically. Missing OCR blocks screenshot processing.

Real task evidence established small-account selection and the server-entry transition. A later
login-invalid notice reported another-device account use; entering gameplay and the requested
in-game interaction remain unverified. The runtime preserves that blocker and identity checkpoint.
`reports/gameplay.json` is a read-only projection of actual calls, waits, actions and outcome events.
