import { statSync } from 'node:fs';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { atomicJson, atomicText, uid, within, type Fields } from '../storage/values.js';
import type { Execution } from '../workflow/execution.js';
import { settledStatuses } from '../workflow/state-policy.js';

function outcome(execution: Execution, runId: string, snapshot: Fields): string {
  const store = execution.store;
  const run = store.run(runId);
  const states = store.events(runId, { kinds: ['run.state'], limit: 1 });
  const unresolved = store.database.all(
    "SELECT action_id,status FROM actions WHERE run_id=? AND status IN ('prepared','unknown','executed') ORDER BY rowid",
    runId,
  );
  const report = {
    run_id: runId,
    task_id: run['task_id'],
    mode: run['mode'],
    snapshot,
    reason: states.at(-1)?.payload['reason'] ?? null,
    recent_call_failures: store.events(runId, {
      kinds: ['call.failed', 'call.incomplete'],
      limit: 20,
    }),
    unresolved_actions: unresolved,
    scope: 'Recorded outcome only; absent evidence does not establish success or coverage',
  };
  const directory = join(store.runDirectory(runId), 'reports');
  atomicJson(join(directory, 'execution.json'), report);
  const escaped = JSON.stringify(report, null, 2)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  const document = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Execution outcome</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Execution outcome</h1><p>Recorded outcomes remain authoritative. Missing evidence is not success.</p><pre>${escaped}</pre></html>`;
  atomicText(join(directory, 'execution.html'), document);
  return document;
}

function workflow(execution: Execution, runId: string, snapshot: Fields): void {
  const store = execution.store;
  const steps = new Map<string, Fields>();
  for (const event of store.events(runId)) {
    if (!event.kind.startsWith('workflow.stage.')) continue;
    const key = text(event.payload['key']);
    if (event.kind === 'workflow.stage.started') steps.set(key, {});
    steps.set(key, { ...steps.get(key), ...event.payload });
  }
  for (const step of steps.values())
    if (step['child_run']) step['child_status'] = store.run(text(step['child_run']))['status'];
  const report = { run_id: runId, steps: [...steps.values()], snapshot };
  atomicJson(join(store.runDirectory(runId), 'reports/workflow.json'), report);
}

export async function publish(execution: Execution, runId: string): Promise<Fields> {
  const store = execution.store;
  const run = store.run(runId);
  if (!settledStatuses.has(text(run['status'])))
    throw new FrameworkError(ErrorCode.REPORT_NOT_READY);
  const state = store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
  let request = store.events(runId, { kinds: ['report.requested'], limit: 1 }).at(-1)?.payload;
  if (!request || request['state_event_id'] !== (state?.event_id ?? null)) {
    request = {
      request_id: uid('report'),
      state_event_id: state?.event_id ?? null,
      business_status: run['status'],
    };
    store.event(runId, 'report.requested', request);
  }
  const started = performance.now();
  const closed = new Set(
    store
      .events(runId, { kinds: ['report.component.finished', 'report.component.incomplete'] })
      .map((event) => event.span_id),
  );
  for (const event of store.events(runId, { kinds: ['report.component.started'] })) {
    if (!event.span_id || closed.has(event.span_id)) continue;
    const payload = {
      ...event.payload,
      status: 'unknown',
      duration_ms: null,
      error: { code: ErrorCode.PREVIOUS_PROCESS_EXITED },
    };
    store.event(runId, 'report.component.incomplete', payload, {
      span_id: event.span_id,
      parent_span_id: event.parent_span_id,
      request_id: event.request_id,
    });
  }
  const publicationId = uid('publication');
  const snapshot = {
    generated_at: new Date().toISOString(),
    business_status: run['status'],
    stage: run['stage'],
    state_event_id: state?.event_id ?? null,
    source_event_seq: store.watermark(runId),
    kind: run['status'] === 'blocked' ? 'checkpoint' : 'final',
  };
  const start = { ...request, publication_id: publicationId, snapshot };
  store.event(runId, 'report.started', start);
  const components = ['outcome'];
  if (run['mode'] === 'gameplay') components.push('gameplay');
  if (run['mode'] === 'workflow') components.push('workflow');
  if (['gameplay', 'workflow'].includes(text(run['mode']))) components.push('exploration');
  components.push('trace', 'context', 'task_index');
  const outcomes: Fields = {};
  let fallback: string | null = null;
  let degraded = false;
  for (const component of components) {
    const span = uid('span');
    const boundary = {
      publication_id: publicationId,
      component,
      capability: `report.${component}`,
    };
    store.event(runId, 'report.component.started', boundary, { span_id: span });
    const componentStarted = performance.now();
    let result: Fields = { status: 'complete' };
    try {
      if (component === 'outcome') fallback = outcome(execution, runId, snapshot);
      else if (component === 'workflow') workflow(execution, runId, snapshot);
      else {
        result = object(await execution.tool('render', runId, { component }));
        if (!['complete', 'failed'].includes(text(result['status'])))
          throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
      }
    } catch (error) {
      result = { status: 'failed', error: frameworkError(error).details() };
    }
    if (result['status'] !== 'complete') {
      degraded = true;
      if (component === 'trace') {
        try {
          let compact = false;
          try {
            const rendered = object(
              await execution.tool('render', runId, { component: 'compact_trace' }),
            );
            compact = rendered['status'] === 'complete';
          } catch (error) {
            result['compact_error'] = frameworkError(error).details();
          }
          if (!compact) {
            fallback ??= outcome(execution, runId, snapshot);
            atomicText(join(store.runDirectory(runId), 'reports/trace.html'), fallback);
          }
          result['fallback'] = 'reports/trace.html';
        } catch (error) {
          result['fallback_error'] = frameworkError(error).details();
        }
      }
    }
    result['duration_ms'] = Math.round(performance.now() - componentStarted);
    outcomes[component] = result;
    store.event(runId, 'report.component.finished', { ...boundary, ...result }, { span_id: span });
  }
  const names = [
    'execution.json',
    'execution.html',
    'trace.html',
    'agent-trace.json',
    'context.html',
  ];
  if (run['mode'] === 'gameplay') names.push('gameplay.json', 'gameplay.html');
  if (['gameplay', 'workflow'].includes(text(run['mode'])))
    names.push('exploration.json', 'exploration.html');
  if (run['mode'] === 'workflow') names.push('workflow.json');
  const files: Fields[] = [];
  for (const name of names) {
    const reference = `reports/${name}`;
    try {
      files.push({
        ref: reference,
        bytes: statSync(within(store.runDirectory(runId), reference)).size,
      });
    } catch {
      degraded = true;
    }
  }
  const receipt: Fields = {
    ...start,
    status: degraded ? 'degraded' : 'complete',
    components: outcomes,
    duration_ms: Math.round(performance.now() - started),
    files,
  };
  try {
    atomicJson(join(store.runDirectory(runId), 'reports/publication.json'), receipt);
  } catch (error) {
    receipt['status'] = 'degraded';
    receipt['receipt_error'] = frameworkError(error).details();
  }
  store.event(runId, 'report.finished', receipt);
  return receipt;
}
