import { object, text } from '@game-automation/contracts';
import { ErrorCode, frameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import { uid, type Fields } from '../storage/values.js';
import type { Execution } from '../workflow/execution.js';

/** Settled-state cleanup is independent of business outcomes and report-only recovery. */
export async function closeGame(
  execution: Execution,
  runId: string,
  manifest: Fields,
): Promise<boolean> {
  const store = execution.store;
  const task = store.input(runId);
  const bound = store.events(runId, { kinds: ['device.session.bound'], limit: 1 }).at(-1);
  if (!bound) return false;
  const state = store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
  const attempted = store.events(runId, { kinds: ['gameplay.lifecycle.started'], limit: 1 }).at(-1);
  if (
    attempted?.payload['operation'] === 'close' &&
    attempted.payload['state_event_id'] === state?.event_id
  )
    return false;
  try {
    const host = Host.fromManifest(store, runId, manifest, AbortSignal.timeout(90_000));
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
  return true;
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
  const children = store.database.all(
    `SELECT r.id FROM runs r JOIN run_inputs i ON i.run_id=r.id
     WHERE r.mode='gameplay' AND json_extract(i.spec,'$.workflow_run')=? ORDER BY r.rowid DESC`,
    runId,
  );
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
    if (await closeGame(execution, child, manifest)) await execution.publish(child);
  }
}

export function recoverFinalization(execution: Execution): void {
  const store = execution.store;
  const rows = store.database.all(
    `SELECT r.id AS run_id FROM runs r WHERE r.mode='gameplay'
     AND EXISTS (SELECT 1 FROM events e WHERE e.run_id=r.id AND e.kind='gameplay.lifecycle.started')`,
  );
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
