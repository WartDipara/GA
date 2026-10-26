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
Both theme modes are validated against `contracts/v1/ui-theme.schema.json` during the normal
build. Edit this configuration, run `npm run build`, and reload the viewer. Theme preference is
local to the browser. Component styles reference semantic tokens rather than defining palettes.
Screenshot evidence is unchanged; only annotation overlays follow the shared theme.

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
