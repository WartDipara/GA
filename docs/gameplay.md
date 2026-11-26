# Game testing workflow

## Workflow and roles

After the verified game launch, `play` enters the TypeScript testing workflow. The same workflow
serves login and interaction, reachable exploration, and native battle. Capture, foreground
return, explicit activation, and caller-supplied routines use the deterministic entry points.
The control plane owns state and device authorization. Plugins use JSON-RPC and handle device
access, image processing, credentials, and model transport as independent capabilities.

| Role | Responsibility |
| --- | --- |
| Test lead | Own the goal, select a Skill, ask for missing context, delegate an observation, or propose one action. |
| Evidence judge | Independently assess the outcome of an action, observed identity or battle state, or the evidence for goal completion. |
| Interface worker | Read visible interface text, controls, and state from one screenshot. |
| Exploration worker | List visible entries and scroll candidates for the current viewport. |
| Comparison worker | Describe observable differences between a before and after image. |

Workers run only within a core Agent's one-question delegation. They return observations,
candidate explanations, and evidence gaps. Each returned fact cites an image passed to the worker.
The workflow handles one device action at a time. It persists the expected result and action
intent before dispatch, then checks local predicates or invokes the evidence judge. An unresolved
action remains in the ledger and blocks new device inputs until reconciled.

## Handoffs and Skills

A handoff includes the task goal, current observation, last verified milestone or interaction,
pending action, and a versioned index of retained execution records. The lead also receives bound
identity, subgoals, recent attempts, settled results, and user supplements. A judge receives the
specific question, action intent, recorded execution, and relevant before and after evidence.
Each core response acknowledges the current handoff version. Key recorded exchanges are provided
up front. Both core roles can request a scoped transcript page by execution, call span, subgoal,
action, or observation, with a cursor for older records or a longer excerpt. Retained public model
messages and submitted inputs can be inspected without relying on the provider session. Missing
historical artifacts are recorded as unavailable.

Core role instructions and the active goal Skill are loaded by default. Entry, exploration,
battle, and recovery Skills can be requested by name. Workers receive only the assigned Skill.
A Skill can expose a game reference at `references/games/<package>/<version-code>.md` under its
own directory. The lead can request the matching reference if one is pinned in the batch. The
manifest digest, implementation files, shared Skills, references, and contracts are checked before
inference. Changed dependencies require a new batch. Submitted inputs preserve the actual text.

The lead receives sanitized images bounded to 1280 pixels on the long edge and 768 pixels on the
short edge. Each image declares its coordinate space. Perception targets and actions are expressed
in image pixels and validated when converted back to native coordinates. Raw screenshots and
credential values remain local. Device command status and authenticated entry are separate facts.
Use `game-auto provide <run_id> --text "..."` to add scoped user clarification before resuming the
owning workflow. Supplements cannot override kernel action restrictions.

Mandatory game or SDK entry terms for the configured test account are covered by the project's
standing authorization. The entry Skill checks required controls, avoids toggling accepted terms,
and distinguishes agreement acceptance from successful login. Optional consent and unrelated
contracts remain outside scope. Explicit task restrictions can narrow the authorization.

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

Pending actions follow the kernel ledger. A failure before dispatch clears the pending proposal
only when no intent was created or the new intent was rejected. Unknown/executed intents retain
their action IDs for reconciliation, including failed cached routines. No new proposal may replace
an unresolved one. When accepted, outcomes and their progress commit through the existing writer before the next
device action. An interrupted verification keeps the pending action available for reconciliation. Unresolved installation
intents return to device preparation instead of being decoded as gameplay actions.

```powershell
uv run game-auto resume <run_id>
uv run game-auto resume <run_id> --new-batch
```

Normal resume requires unchanged code and shared Skills. Use `--new-batch` after an update: it links a new batch
to the same task and restores scoped progress. A credential profile replaced in place cannot silently
reuse another account's progress. Stable recovery categories have a persisted two-round limit.
Three repeated state/goal observations enter stall handling.

Time limits still apply to individual commands and model requests. A workflow invocation allows
up to 1,000 cycles before a resume is required. Optional `--max-turns <n>` also bounds the number of
lead decisions. At either limit, the workflow can settle pending work and stops new dispatches.

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

Gameplay JSON/HTML show agent nodes, decisions, action paths, before and after evidence, AI calls,
deterministic steps, waiting, correction and cache statistics. Rebuilding them never contacts devices
or models. Large traces use bounded HTML previews; complete events remain in SQLite and `trace`
streams them. Large context views identify their limited event window. The existing context
projection remains inspired by dsh-context.

Each action exposes `execution_duration_ms` and `execution_status`, joined by its span to the
host's existing monotonic call timer. Timing includes device execution checks and plugin overhead;
it excludes AI node calls, subsequent waiting, log collection and outcome review. Failed/incomplete
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
