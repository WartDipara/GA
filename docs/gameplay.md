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
