import { object, text } from '@game-automation/contracts';
import { ErrorCode, frameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import { uid, type Fields } from '../utilities/values.js';
import { actionReserveMs } from '../workflow/deadline.js';
import type { Execution } from '../workflow/execution.js';

/** Settled-state cleanup is independent of business outcomes and report-only recovery. */
async function closeGame(
  execution: Execution,
  runId: string,
  manifest: Fields,
): Promise<void> {
  const store = execution.store;
  const task = store.input(runId);
  const bound = store.events(runId, { kinds: ['device.session.bound'], limit: 1 }).at(-1);
  if (!bound) return;
  const instance = text(task['instance_id']);
  const apkKey = `${String(task['source_run'])}:${String(task['apk_index'])}`;
  const remaining = execution.deadline.remaining();
  const unresolved = store.actionRecords.unresolvedForWorkspace(instance, apkKey);
  const state = store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
  const attempted = store.events(runId, {
    kinds: ['gameplay.lifecycle.started', 'gameplay.lifecycle.skipped'], limit: 1,
  }).at(-1);
  if (
    attempted?.payload['operation'] === 'close' &&
    attempted.payload['state_event_id'] === state?.event_id
  )
    return;
  if (unresolved.length || (remaining !== null && remaining < actionReserveMs)) {
    const skipped = {
      operation: 'close',
      status: 'skipped',
      reason: unresolved.length ? 'unresolved-action' : 'task-deadline',
      action_ids: unresolved.map((action) => action.action_id),
      remaining_ms: remaining,
      state_event_id: state?.event_id ?? null,
    };
    store.event(runId, 'gameplay.lifecycle.skipped', skipped);
    return;
  }
  try {
    const limit = AbortSignal.timeout(90_000);
    const signal = remaining === null ? limit : AbortSignal.any([limit, execution.signal]);
    const host = Host.fromManifest(store, runId, manifest, signal);
    const input = {
      session: bound.payload['request'],
      package_name: task['target'],
      operation: 'close',
      sha256: object(task['metadata'])['sha256'],
    };
    await host.lifecycle(input);
  } catch (error) {
    store.event(runId, 'gameplay.finalization.issue', {
      error: frameworkError(error).details(),
      business_status: store.run(runId)['status'],
      game_closed: 'unknown',
      state_event_id: state?.event_id ?? null,
    });
  }
}

export async function finalizeGames(
  execution: Execution,
  runId: string,
  manifest: Fields,
): Promise<void> {
  const store = execution.store;
  const task = store.input(runId);
  if (store.run(runId)['mode'] === 'gameplay') {
    if (!['capture', 'activate-game', 'return-to-game', 'routine'].includes(text(task['goal'])))
      await closeGame(execution, runId, manifest);
    return;
  }
  if (store.run(runId)['mode'] !== 'workflow') return;
  const children = store.workflows.children(runId, 'gameplay');
  const instances = new Set<string>();
  for (const row of children) {
    const child = text(row['id']);
    const instance = text(store.input(child)['instance_id']);
    if (
      instances.has(instance) ||
      !store.events(child, { kinds: ['device.session.bound'], limit: 1 }).length
    )
      continue;
    instances.add(instance);
    await closeGame(execution, child, manifest);
  }
}

export function recoverFinalization(execution: Execution): void {
  const store = execution.store;
  const rows = store.workflows.lifecycleRuns();
  for (const row of rows) {
    const runId = text(row['run_id']);
    const start = store.events(runId, { kinds: ['gameplay.lifecycle.started'], limit: 1 }).at(-1);
    if (
      !start?.span_id ||
      store.events(runId, {
        kinds: ['gameplay.lifecycle.finished', 'gameplay.lifecycle.incomplete'],
        span: start.span_id,
        limit: 1,
      }).length
    )
      continue;
    store.event(
      runId,
      'gameplay.lifecycle.incomplete',
      {
        ...start.payload,
        status: 'unknown',
        duration_ms: null,
        error: {
          code: ErrorCode.PREVIOUS_PROCESS_EXITED,
          message: 'Device state requires fresh inspection',
        },
      },
      { span_id: start.span_id, parent_span_id: start.parent_span_id },
    );
    const state = store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
    store.event(runId, 'report.requested', {
      request_id: uid('report'),
      state_event_id: state?.event_id ?? null,
      business_status: store.run(runId)['status'],
    });
  }
}
