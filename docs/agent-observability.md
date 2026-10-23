# Agent observability

The primary view follows recorded calls and their evidence. It projects the existing SQLite event
store; it does not create another logging database. The structure takes inspiration from
[Tencent Agent Trace](https://cloud.tencent.com/document/product/614/136743), while preserving this
framework's event identifiers, execution boundaries and task ownership. It is an application
projection, not an OTLP exporter or a claim of wire compatibility.

## Call chains

Run `uv run game-auto serve-trace <run_id>` and open a stage execution from the task workspace.
The execution view polls committed evidence every three seconds. It includes:

- An expandable waterfall of workflow stages, ReAct steps, Codex turns and framework tool calls.
- Recorded parent IDs, request IDs, retry links, provider session/turn IDs and linked executions.
- Exact retained input/output JSON loaded on expansion, and a paginated event timeline per node.
- Measured durations, errors, decision explanations, actual usage and explicit incomplete states.
- Before/after screenshots with native-coordinate target markers on the before image.

The task ID is the logical trace ID. Each execution has a derived root identified by its run ID.
Calls and steps preserve their native span IDs. Workflow children and reused executions are links;
historical evidence is not silently reparented into a new batch. Unavailable parents are explicitly
identified. A derived root does not have an invented execution duration.

The host automatically attaches calls and business events to the active step. Each new ReAct step
records a monotonic duration and a completion, failure or interruption event. Each retry retains its
own span and links to the previous attempt. Existing records without an ending remain incomplete
after their execution stops. A completed step indicates a closed scheduling interval, not a verified
game objective. Only recorded outcome evidence establishes verification.

Waterfall bars represent recorded wall-clock boundaries. Duration fields represent measured
execution time. Nested durations are not added together. Model turns describe `ai.decide` App Server
turns; they do not claim visibility into internal model request counts or hidden reasoning.

The semantic view retains the latest 20,000 relevant events and declares when that window is full.
The underlying event store and CLI trace retain earlier evidence. Context details preserve the
existing context dashboard and raw-event view. Device commands remain in node event evidence
rather than becoming thousands of top-level waterfall rows.

```powershell
uv run game-auto agent-trace <run_id>
uv run game-auto inspect <run_id> --span <span_id>
uv run game-auto export-trace <run_id> --format html
```

Export writes `trace.html`, `agent-trace.json` and the context view under the execution's reports
directory. The JSON contract is `contracts/v1/agent-trace.schema.json`. Opening an exported page or
expanding a record never calls a model or performs a device action.

## Action evidence

An SVG overlay scales with the original screenshot using its native dimensions. A white ring and
red center mark the input point; a gold rectangle marks recorded target bounds. Swipe arrows show
direction, and long presses have a second ring. The original evidence bytes remain unchanged.

Dispatched targets require an actual `action.dispatched` event. Undispatched proposals use dashed
markers. Neither a proposal nor dispatch establishes success. The after image is the recorded
outcome observation and does not reuse the before image's coordinates. Missing screenshots and
out-of-bounds recorded points remain visible limitations. The view renders at most 2,000 recent
action cards; retained action references remain available in JSON and SQLite.

## Session replay

Turns are grouped by the provider's reported session ID. Each turn exposes submitted Skills,
attachments and input references, public model items, structured results and related framework
tools. Provider-issued Tool Calls and framework tool invocations remain distinct. Missing session
IDs are labeled unassociated. Replay reconstructs retained exchanges and never reissues tools.

The application request is not the provider's hidden server context. Public reasoning summaries
remain optional. Actual provider protocol messages remain in event evidence; summary availability
does not determine whether the application call chain can be inspected.

## Performance

The performance view aggregates recorded calls, errors, incomplete calls, nearest-rank P50/P95
latencies and timing coverage. Error rate uses failed / (completed + failed), excluding incomplete
calls. Groups use reported Agent role, configured model and an optional opaque caller reference.
No Windows username or game account is inferred for these dimensions.

```powershell
uv run game-auto trace-owner <run_id> --actor-ref <opaque_reference>
```

Token attribution uses observed provider session-counter deltas. A fresh session establishes a
known zero baseline. Historical unknown baselines and counter resets remain unknown; cumulative
session totals are not added repeatedly. Reported last-turn values remain visible separately.

Costs are optional secondary reference data. `config/pricing.toml` contains a dated, exact-model
OpenAI API scenario and its official source. Codex CLI account charges are not available here.
Missing prices or required counters produce an unavailable estimate, not zero. These estimates do
not represent subscription charges or invoices. The default view prioritizes execution evidence.

## Evidence-backed analysis

The browser prepares a command from a natural-language question. The CLI explicitly creates a
separate analysis execution under the same task, using the existing Codex adapter and the
`trace-analysis` Skill. Viewing a page does not launch inference.

```powershell
uv run game-auto analyze-trace <run_id> --question 'Which operations caused the most delay?'
```

The analyst receives metrics, quality coverage and a bounded selection of recorded spans. Omitted
nodes are declared. Every finding must cite a span or event actually included in that input.
Structured findings include an explanation, recommendation, candidate change and validation plan.
They do not modify prompts, code, models or devices. Results appear in the source execution's
analysis view, while the analysis request has its own trace. External questions and evidence remain
task data and cannot grant tool permissions.

## Quality and evolution

Automatic evaluators describe operational facts: call completion, verified action rate, recorded
business goals and unclosed spans. These are not ground-truth model accuracy. Accuracy, tool
correctness and task completion use explicitly supplied human labels with visible denominators.

```powershell
uv run game-auto review <run_id> --span <span_id> --metric tool_correctness --label pass --evidence <event_id> --note 'Reviewed against the retained outcome'
uv run game-auto export-dataset <task_id>
uv run game-auto compare-traces <first_run_id> <second_run_id>
```

Reviews append immutable events and reference existing spans and events. The latest label per
execution, span, metric and reviewer determines the derived dataset. Unknown labels are excluded
from scored cases. Separate reviewers remain separate judgments, not automatically adjudicated
ground truth. The task-owned `datasets/agent-reviews.jsonl` contains labels, version fingerprints
and evidence references without duplicating screenshots or prompts. Task cleanup removes it with
the task. Earlier annotations remain in SQLite.

Version comparison shows implementation/Skill fingerprints, quality and performance coverage for
two executions. Missing fingerprints remain unknown. Different task difficulty or sample coverage
can explain differences; the comparison does not establish that a prompt caused an improvement.
The dataset and findings support reviewed prompt/model changes followed by a new execution batch.
No automatic prompt deployment or model training is performed.

Development verification remains Python compilation and JavaScript syntax checks. Inspecting
previously recorded real game evidence verifies its presentation only. New lifecycle instrumentation
and natural-language analysis require a future explicitly requested business execution for runtime
acceptance; no synthetic inference calls are used to verify this increment.
