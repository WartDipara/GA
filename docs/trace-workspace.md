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

`packages/trace-ui` provides a React shell and charts over the existing read-only projections.
`config/ui-theme.json` is the single palette, typography, spacing, radius and motion configuration.
Both theme modes are validated against `contracts/v1/ui-theme.schema.json` during the normal
build. Edit this configuration, run `npm run build`, and reload the viewer. Theme preference is
local to the browser. Component styles reference semantic tokens rather than defining palettes.
Screenshot evidence is unchanged; only annotation overlays follow the shared theme.

The built JavaScript and CSS live once in `var/cache/typescript/`. Reports reference these shared
assets, rather than copying React or a theme into each execution. Keep this directory alongside
retained reports for offline viewing. The service exposes only the two exact UI asset paths;
dependencies and fonts are local, with no CDN requests. Schema validation is precompiled so the
browser does not require `unsafe-eval`. If enhancement loading fails, the original report content
and navigation remain available.

History charts cover all recorded executions, independent of the task table's filters. Execution
charts show recorded module-call durations, their median and nearest-rank P95. Unknown durations
are excluded and counted explicitly. Slow-call bars link directly to their span in the call trace.
Nested spans overlap, so these measurements are never summed into task elapsed time. Chart
projections use `contracts/v1/workspace-view.schema.json`, refresh every three seconds, and retain
the last valid snapshot during a connection failure. They do not write state or generate evidence.

Transitions run on entry and view changes, without replaying on polling updates. Reduced-motion
preferences disable them. Layouts adapt to narrow windows, and keyboard focus remains visible.
Design references include [Langfuse trace navigation](https://langfuse.com/docs/observability/overview)
and [Grafana trace drill-down](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/traces/).
