# Trace workspace

Start the local, read-only viewer independently of a game task:

```powershell
npm run trace
npm.cmd run trace -- <run_id> --port 0
```

The Node launcher supervises the existing Python renderer service using the project's virtual
environment. It never opens a state writer or starts a model or device. It can start before the
first task exists and discovers later tasks through SQLite. The service is loopback-only and
polls committed records. Stopping it does not stop a workflow.
Use `npm.cmd` when forwarding flags in PowerShell so npm receives the argument separator unchanged.

## Recording and failure boundaries

The plugin host records calls, input/output references, measured durations, errors and retries in
code. The TypeScript store owns event sequencing, validation, deduplication and transactional
state updates. Provider messages are captured by the adapter; model decisions and public summaries
are recorded content, not evidence that a suggested action executed or succeeded. Missing timings,
usage and endings remain unknown or incomplete. No model fills gaps or writes the factual log.

Metrics, session replay, HTML reports and periodic viewer refresh are deterministic projections.
They do not invoke AI. `analyze-trace` is a separate, explicitly requested AI analysis of retained
evidence; its conclusions do not replace source events or become prerequisites for logging.

Provider wire messages and their normalized events can share a bounded batch transaction; the
host acknowledges persistence only after commit. Calls retain large inputs/outputs as referenced
files. Critical records are not sampled away. SQLite uses WAL with full synchronization, so
durability has real disk cost; bounded batching reduces repeated commits without claiming zero
overhead. Resource-use or latency guarantees require measurements on the actual workload.

A trace or evidence write failure prevents new device actions. Unknown action outcomes must be
observed and reconciled before continuing. Report components fail independently, retain fallback
outcome records where possible and support report-only recovery after storage becomes available.
The read-only viewer can stop or fail without stopping the workflow's authoritative recording.

Inspect writer overhead or retained evidence without starting a writer, model or device:

```powershell
uv run game-auto trace-health <run_id>
uv run game-auto trace-doctor <run_id>
```

Writer health snapshots are emitted at most once every five seconds at recording boundaries,
before trace rendering and after report publication. Measurements are cumulative per
`collector_id`; retain the latest snapshot per collector when aggregating. Transaction, commit,
encoding and artifact-write timings
overlap and must not be added together. Encoded bytes are not physical disk IO. Older executions
without measurements return `null`, not zero. Health records exclude their own current write.
The doctor scans committed call boundaries and referenced files with a bounded findings list;
counts include findings beyond that limit. Open or incomplete calls and unresolved actions remain
explicit. Its report links identify the corresponding spans; it never repairs or replays actions.

Codex text and public-summary deltas use a bounded buffer: up to 16 events or 256 KiB, flushed
after at most 50 ms while the writer is available. A single larger event is flushed on its own,
up to 4 MiB. Backpressure waits for persistence rather than growing the queue. Original fragments,
capture timestamps and provider sequence numbers are retained; batching does not sample content.
Consecutive normalized deltas for the same item are combined within a batch, with references to
every original wire event; the wire messages themselves remain unchanged.
Outbound requests and non-streaming boundaries flush queued fragments before proceeding. Turn
completion and adapter shutdown also flush, and persistence failure prevents a decision from
returning successfully. An abrupt process crash can leave the last uncommitted fragments unknown;
recovery must not fabricate them. `ai.trace.buffer` records actual batch and queue measurements.
The JSON-RPC peer also bounds queued bytes, pending requests and active handlers.

The live service folds only events after each cached run's watermark. Its connection-free LRU
retains at most four folds and 8 MiB of serialized fold data; oversized folds remain rebuildable
without entering the cache. An older request snapshot never consumes a newer cached fold.
The single writer creates indexes for run/kind/sequence and run/span/sequence queries at startup.
The viewer never creates indexes or writes projections to the business database.

Live call pages load conversation bodies and action figures only when their views are opened.
Polling revisions ignore transport-only fragments that do not change the displayed projection.
Unchanged revisions return 204; hidden or closed tabs cancel pending refreshes and polling.
Offline exports retain complete views and do not require the live service. Writer health is shown
under Performance, with missing historical measurements explicitly unavailable.

Each invocation or resume records an execution segment with its own attempt ID and monotonic
duration. Calls attach to that segment unless they already have a more specific parent. Later
segments link to earlier segments; crash recovery explicitly closes interrupted segments with an
unknown duration. Report publication remains outside the measured gameplay segment. Segment
measurements never include time spent blocked between invocations, and nested spans must not be
summed into elapsed time. Historical executions without segments retain unknown measurements.
The top-level gameplay duration sums completed, measured segments from that execution only.
It excludes blocked waiting, report publication and nested call durations. The inspector shows
segment coverage and any measured portion separately. A running or interrupted segment, missing
or inconsistent timing, or execution activity outside recorded segments keeps the total unknown;
resuming an older execution never treats its missing history as zero. Report-only rebuilding
uses the same recorded segments and cannot change the measured gameplay duration.
Before starting a resumed segment, the writer also closes an unfinished prior segment even if
its business state had already settled before the original process stopped. The duration remains
unknown; an observed old start is never converted into a successful ending.

Session replay also includes a paged business-task timeline across execution batches. Select a
segment to open its call span, or another execution to inspect its model session. The timeline
shows the latest 20 segments per execution with an explicit truncation notice; the call trace
retains the complete segment chain. Read the same projection from the CLI:

```powershell
uv run game-auto trace-session <run_id> --limit 50
uv run game-auto trace-session <run_id> --before <next_before> --limit 50
```

## Retention and metadata export

`[observability]` in `config/application.toml` controls `retention_days` and `export_max_bytes`.
Age-based selection is disabled by default (`retention_days = 0`). Retention removes whole tasks,
preserving their evidence relationships until explicit cleanup rather than pruning individual
debug events. Preview first; applying uses the existing single-writer cleanup journal and removes
task-owned SQLite rows and directories together. No timer or background purge is installed.

```powershell
uv run game-auto trace-retention --days 30
uv run game-auto trace-pin <run_id> --pin
uv run game-auto trace-pin <run_id> --unpin
uv run game-auto trace-retention --days 30 --apply
uv run game-auto trace-export <run_id>
```

Pinned, running, queued or blocked tasks, unknown-age tasks, unresolved actions, unfinished reports
and dependencies of retained tasks are protected from age-based selection. Each application
removes at most 200 tasks, ordered so remaining tasks cannot lose their dependencies. Dependency
cycles require an explicit scoped cleanup. Pins protect this retention policy; direct manual
`cleanup` remains an explicit operator action. This release does not rewrite historical records
to migrate raw debug events into cold storage.

`trace-export` writes `reports/exports/trace.otlp.v1.json.gz` and an atomic receipt under the
owning execution. JSON encoding is streamed through a bounded compression buffer, respects the
configured uncompressed size limit and preserves the configured disk reserve. Existing exports
are replaced atomically; the source trace is unchanged. The export uses a versioned deterministic
mapping to OTLP Trace/Span IDs and a strict metadata allowlist. Prompts, responses, input/output
bodies, screenshots, account references, machine paths and host identifiers are excluded. Closed
spans with valid recorded timestamps are exported; missing or invalid boundaries are listed as
omissions, never invented. Measured execution duration remains a separate attribute from OTLP's
wall-clock boundaries. Exports are local files; no collector, network upload or second event
writer is started. Format reference: [OTLP/JSON](https://opentelemetry.io/docs/specs/otlp/).

## Find a game or task

The task-history table has a sticky search field. Type any part of a recorded game name, package
name or task ID; results update without Enter or a submit button. Matching ignores case, normalizes
Unicode and combines space-separated terms. It matches substrings, without guessing misspellings.
Status filters compose with the query. Search and pagination remain in the URL for return navigation.

One persistent browser Web Worker holds the compact search index; it never receives call logs or
screenshots. Each input schedules the latest query at the next animation frame. There is at most
one in-flight query and one pending replacement, and stale query/data revisions cannot update the
table. Only a page of at most 50 row indices returns to the UI. Narrower queries reuse the previous
matches; page changes reuse the current result set. No HTTP request or thread is created per key.

History metadata is selected in one batch query rather than one query per task. The three-second
refresh uses an ETag to skip unchanged response bodies and rebuilds the index only when metadata changes.
Navigation terminates the worker and pending polling; returning through the browser cache restores
them. If workers are unavailable or fail, the same matcher scans in cooperative chunks on the main
thread and keeps the last visible results while updating. This is a read-only projection and does
not change task execution, trace persistence or the single state writer.

Heavy native computation remains a capability boundary: add Node-API/C++ only for an observed CPU
bottleneck with measured benefit. Do not share a live SQLite connection or mutable business state
across that boundary. The current search needs no native addon or shared-memory protocol; immutable
index snapshots and small query/results messages avoid cross-thread ownership and synchronization
hazards.

## Navigation

The live sidebar pre-renders retained tasks, executions and report links as nested disclosure
groups. Indentation and connecting rules show the hierarchy. Expanding a group needs no request; the
selected report's ancestors stay open. Expansion and sidebar scroll are remembered in the browser
tab across navigation and Back. Offline reports retain a compact contextual navigation instead of
copying the entire history into every file.

Task history links to a task overview and its live workspace. Each execution has an overview,
call trace, model context, available action/element views and an outcome. Every report has the
same task/history links, previous/next execution links and a return link. Model turns and actions
link to their call spans; parent and related spans are navigable. URL fragments preserve selected
spans, views and exploration filters. Cross-batch action evidence uses the action's owning run.

The evidence inspector displays retained JSON, text and screenshots without leaving the report
navigation. Large previews are bounded; downloads retain the complete file. Missing records link
back to task history. Historical runs can be viewed through dynamic read-only projections without
running their workflows again.

## Stored structure

```text
var/
  state.sqlite                         # Authoritative business events and state
  reports/history.html                 # Rebuildable cross-task history
  reports/history.json
  tasks/<task_id>/
    task.json
    reports/task.html                  # Task execution index
    reports/task.json
    executions/<run_id>/
      manifest.json                    # Pinned implementation and configuration
      calls/<span_id>/                  # Retained request/response files
      artifacts/                       # Sanitized images and other evidence
      reports/
        index.html                     # Execution overview
        index.json                     # contracts/v1/execution-index
        trace.html
        agent-trace.json
        context.html
        gameplay.html                  # Gameplay executions
        exploration.html               # Gameplay/workflow executions
        execution.json
        publication.json
```

The TypeScript publication pipeline automatically generates the execution index and linked reports
after success, failure, cancellation or a blocked checkpoint. The index records source event
sequence, counts, related executions and relative evidence directories. These are projections of
existing records; no second business log, duplicated event stream or per-step log file is created.
Report failures retain the business outcome and pending publication. `game-auto report <run_id>`
repairs reports independently, without replaying work. Cleanup refreshes the shared history after
removing task-owned records and files. The live history always reads current SQLite records.

Runtime acceptance remains separate: report availability does not establish game completion,
coverage, translation-hook delivery or correctness of a model's decision.

## Shared UI and theme

Reports render their shared shell from `reporting/web/workspace-shell.template.html` before the
first paint. `packages/trace-ui` enhances charts over the existing read-only projections.
`config/ui-theme.json` is the single palette, typography, spacing, radius and motion configuration.
Both theme modes are validated against `contracts/v1/ui-theme.schema.json` during the normal build.
Edit this configuration, run `npm run build`, and reload the viewer. Its Gothic defaults use
obsidian surfaces, purple panels, fine gold borders, angular controls and local serif fonts; the
light mode uses parchment surfaces. The visual reference is
[StyleKit Gothic](https://www.stylekit.top/zh/styles/gothic). Component styles reference semantic
tokens rather than defining palettes. Screenshot evidence is unchanged; only annotation overlays
follow the shared theme.

Open **Appearance** in the header to choose a theme, adjust text size or reduce motion. Text scaling
includes navigation, tables, controls and report details. Settings apply immediately and persist
locally across reports and tabs. **Project default** follows the configured theme; **Reset
defaults** restores the configuration's appearance values. OS reduced-motion settings remain
respected. Restoring preferences runs before painting, including retained report pages served by the
viewer; it never modifies the business records.

The built JavaScript and CSS live once in `var/cache/typescript/`. Reports reference these shared
assets, rather than copying React or a theme into each execution. Keep this directory alongside
retained reports for offline viewing. The service exposes only the exact UI and search-worker assets;
dependencies and fonts are local, with no CDN requests. Schema validation is precompiled so the
browser does not require `unsafe-eval`. Theme restoration runs immediately after the CSP declaration,
before styles or content are painted. Navigation and layout do not wait for React. Shell styles are
scoped to their own container so report-specific element selectors cannot alter their geometry.
If enhancement loading fails, report content, theme selection and navigation remain available.
The viewer validates shared assets with ETags, reusing unchanged bundles while detecting rebuilds.

History charts cover all recorded executions, independent of the task table's filters. Execution
charts show recorded module-call durations, their median and nearest-rank P95. Unknown durations
are excluded and counted explicitly. Slow-call bars link directly to their span in the call trace.
Nested spans overlap, so these measurements are never summed into task elapsed time. Chart
projections use `contracts/v1/workspace-view.schema.json`, refresh every three seconds, and retain
the last valid snapshot during a connection failure. They do not write state or generate evidence.

Supporting browsers use [native cross-document transitions](https://developer.chrome.com/docs/web-platform/view-transitions/cross-document)
for same-origin report navigation. Other browsers retain normal links. Neither path replaces document
lifecycles, polling cleanup, return links or URL fragments. Content movement never fades the whole
page to its background and does not replay on polling updates. Reduced-motion preferences disable
transitions. Layouts adapt to narrow windows, and keyboard focus remains visible.
Design references include [Langfuse trace navigation](https://langfuse.com/docs/observability/overview)
and [Grafana trace drill-down](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/traces/).
