# Hybrid gameplay

## Module boundaries

| Component | Boundary |
| --- | --- |
| DeviceRuntime | Bound process/task/display observations and kernel-authorized input |
| Perception | Local frame comparison, accessible candidates and explicit unknown results |
| ProgressStore | Identity, prerequisite-ordered goals, milestones and durable checkpoints |
| DecisionRouter | Deterministic selection between a guarded routine and planning |
| Planner | Structured Codex decisions using pinned shared Skills and sanitized images |
| RoutineExecutor | One guarded proposal at a time, at most five steps |
| OutcomeVerifier | Local predicates, followed by semantic review when required |
| EvidenceRecorder | Existing host tracing, screenshot admission and process-scoped logs |

The kernel owns device authority and business state. Plugins exchange JSON-RPC data and never
read each other's implementations or the business database. Login, exploration and native battle
share one observation/decision/execution loop. Public reasoning summaries remain optional.

The planner receives sanitized images bounded to 1280 pixels on the long edge and 768 pixels on
the short edge. Each image declares its coordinate space. Perception targets and the pending
action are projected into their corresponding image coordinates; proposed points and rectangles
are validated and converted back to native display pixels before authorization. Original device
screenshots remain unchanged. Traces retain the submitted image geometry, model coordinates and
native decision, so a scale or rotation problem can be diagnosed without guessing where a tap went.
Moving an existing task to this explicit coordinate space invalidates its old stall counter once;
identity, action outcomes and recovery limits are preserved.

Planning context includes the most recent action's actual execution status and result. A rejected
action with no dispatch must not be described as completed. Credential actions focus a point inside
the declared field bounds; values are resolved locally from the credential profile. Masked fields
do not establish an account mismatch or failed input. Input command completion and authenticated
game entry remain separate outcomes.

## Prepare and run

Choose an already-running dedicated MuMu instance from `game-auto emulators`. Its index identifies
a simulator, not a game character. Discovery prints its name and index. The provider resolves the
advertised ADB endpoint and verifies stable identity; the kernel never guesses a port.

```powershell
uv run game-auto prepare-device --from-run <apk_run> --apk-index 0 --instance <index>
uv run game-auto credentials default
uv run game-auto play --prepared-run <device_run> --goal login-and-interact
uv run game-auto play --prepared-run <device_run> --goal explore
uv run game-auto play --prepared-run <device_run> --goal advance-battle
```

Add `--adopt-existing` to preparation only for an already installed matching APK. Full SHA-256
must match; application data and cached login are preserved. No fictitious install is recorded.
Credentials are entered at hidden prompts and encrypted for the current Windows user with DPAPI.
Only the profile reference reaches the model and trace. Credential input uses an owned ADB process's
stdin, suppresses output, and currently supports ASCII values without whitespace.

`capture` and `return-to-game` are explicit observation/foreground goals. `activate-game` permits a
controlled start after confirmed process exit. Returning verifies the original process lifetime;
a replacement process cannot silently count as a successful foreground return. Emulator creation
and automatic restart are outside this release.

## Guards, progress and recovery

Actions bind an APK workspace, instance, boot, process lifetime, focused display, rotation and current
target region. Every intent is durable before dispatch authorization. Fresh target comparison
rejects obsolete proposals. A transport error after dispatch remains uncertain.

Resume observes the device first. Recognition caches, server progress and execution history remain
separate data. Unknown resource-consuming actions are never replayed. Observed deviations may be
reconciled without claiming the expectation succeeded. The original small account, server and role
remain bound across batches; identity changes require attention.

```powershell
uv run game-auto resume <run_id>
uv run game-auto resume <run_id> --new-batch
```

Normal resume requires unchanged code and shared Skills. Use `--new-batch` after an update: it links a new batch
to the same task and restores scoped progress. A credential profile replaced in place cannot silently
reuse another account's progress. Stable recovery categories have a persisted two-round limit.
Three repeated state/goal observations enter stall handling.

There is no default overall duration or planning-turn limit. Individual commands/model requests
are bounded. Optional `--max-turns <n>` bounds one invocation; its last planning round does not
dispatch another input requiring a further verification round.

## Local execution and native battle

Perception returns frame differences and accessible targets. Canvas semantics remain unknown when
local evidence cannot identify them. Stability does not establish loading completion. Known
transition and battle waiting use local observations without per-frame model calls.

`--goal routine --routine <file.json>` accepts `gameplay-routine`: at most five steps, each with a
screen guard and explicit before/after predicates. Learned routines additionally require exact
sanitized image matching and bind APK, identity, process and layout. Only observed, verified,
low-risk tap/back transitions are learned. Sensitive screens and resource consumption are excluded.
Mismatch exits the fast path. No game-specific coordinates or scripts exist in the public core.

Native battle prefers existing automatic combat/navigation. Advancement requires an observed
victory settlement and a subsequent changed stage marker. Skill selection, waiting and disappearing
enemies do not establish a cleared level. Real payment, external recharge and account deletion are
prohibited; in-game resources are permitted within the task.

## Evidence and reports

Known credentials are removed by local Windows OCR before screenshot persistence. Identified input
fields and declared sensitive regions are masked geometrically. OCR is a best-effort detector, not
a guarantee for arbitrary private content. Unavailable privacy processing blocks capture.
Screenshots remain local except sanitized images explicitly submitted to the selected model.

Evidence defaults to 4 GiB plus a 2 GiB free-space reserve. Admission includes artifacts and a
conservative allowance for SQLite events/indexes. Exhaustion retains the last durable checkpoint
and blocks new actions. Logcat windows use verified package PIDs and monotonic process start times.
Unsupported formats, changed lifetimes and isolated renderers are explicit limitations. Emulator
and system processes are not mixed into the game stream.
Windows are bounded snapshots, not a claim of complete continuous log capture.

```powershell
uv run game-auto report <run_id>
uv run game-auto export-trace <run_id> --format html
uv run game-auto compare-games <first_run> <second_run>
```

Gameplay JSON/HTML show decisions, action paths, before/after evidence, outcome sources, AI calls,
deterministic steps, waiting, correction and cache statistics. Rebuilding them never contacts devices
or models. Large traces use bounded HTML previews; complete events remain in SQLite and `trace`
streams them. Large context views identify their limited event window. The existing context
projection remains inspired by dsh-context.

Each action exposes `execution_duration_ms` and `execution_status`, joined by its span to the
host's existing monotonic call timer. Timing includes device execution checks and plugin overhead;
it excludes AI planning, subsequent waiting, log collection and outcome review. Failed/incomplete
calls retain their elapsed time. Missing terminal records remain unknown, never zero. The HTML
report lists the 20 longest measured actions and shows timing beside each action record. Rebuilding
older reports uses their original call measurements without operating the device.

Cross-game comparison checks different package identities, frozen runtime sources and actual goal
completion. Equal code hashes alone do not establish acceptance. Same-role restoration and a panel
path have real evidence. Native combat/skill selection have real evidence; verified stage advancement,
cache efficiency and second-game acceptance remain separate gates.

The pinned Codex adapter uses App Server stdio and an isolated HTTP Responses provider with existing
OpenAI authentication, leaving global settings unchanged. See the
[official provider configuration](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).
Development verification uses compilation/syntax checking only. No mock, smoke or synthetic
inference tests are used. Bounded real game tasks retain separate acceptance evidence.
