import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import type { Store } from '../storage/store.js';
import { integer, parsed, within } from '../storage/values.js';

type Element = Extract<ContractMap['exploration-record'], { kind: 'element' }>;
type Proof = NonNullable<Element['data']['verification']>;
type Verification = { status: 'verified'; proof: Proof } | { status: 'unknown'; reason: string };

function unknown(reason: string): Verification {
  const result: Verification = { status: 'unknown', reason };
  return result;
}

export function elementVerification(
  store: Store,
  workspace: string,
  element: Element,
  actionId = element.data.verification?.action_id ?? element.data.action_id,
): Verification {
  if (!actionId) return unknown('missing-action-link');
  const taskId = workspace.split(':')[0];
  if (!taskId) return unknown('missing-task-scope');
  const action = store.database.one(
    'SELECT a.run_id,a.status,a.result FROM actions a JOIN runs r ON r.id=a.run_id WHERE a.action_id=? AND r.task_id=?',
    actionId,
    taskId,
  );
  if (!action || action['status'] !== 'verified') return unknown('action-not-verified');
  const runId = text(action['run_id']);
  const details = parsed(action['result']);
  const outcome = object(details['result']);
  const afterId = outcome['observation_id'];
  const afterRun = outcome['observation_run'];
  if (typeof afterId !== 'string' || typeof afterRun !== 'string')
    return unknown('missing-post-action-observation');
  const binding = store.database.one(
    "SELECT seq,data FROM events WHERE run_id=? AND kind='exploration.element.updated' AND json_extract(data,'$.payload.workspace_key')=? AND json_extract(data,'$.payload.record.id')=? AND json_extract(data,'$.payload.record.status')='in_progress' AND json_extract(data,'$.payload.record.data.action_id')=? ORDER BY seq LIMIT 1",
    runId,
    workspace,
    element.id,
    actionId,
  );
  if (!binding) return unknown('missing-element-action-binding');
  const bindingEvent = decode('trace', parsed(binding['data']));
  const bound = decode('exploration-record', bindingEvent.payload['record']);
  if (bound.kind !== 'element') return unknown('invalid-element-action-binding');
  const beforeId = bound.data.observation_id;
  const before = store.database.one(
    "SELECT seq,data FROM events WHERE run_id=? AND kind='gameplay.observation' AND json_extract(data,'$.payload.observation_id')=? ORDER BY seq LIMIT 1",
    runId,
    beforeId,
  );
  const after = store.database.one(
    "SELECT e.seq,e.data FROM events e JOIN runs r ON r.id=e.run_id WHERE e.run_id=? AND r.task_id=? AND e.kind='gameplay.observation' AND json_extract(e.data,'$.payload.observation_id')=? ORDER BY e.seq LIMIT 1",
    afterRun,
    taskId,
    afterId,
  );
  const dispatches = store.database.all(
    "SELECT seq FROM events WHERE run_id=? AND kind='action.dispatched' AND json_extract(data,'$.payload.action_id')=?",
    runId,
    actionId,
  );
  const dispatch = dispatches[0];
  const settled = store.database.one(
    "SELECT seq,data FROM events WHERE run_id=? AND kind='action.result' AND json_extract(data,'$.payload.action_id')=? AND json_extract(data,'$.payload.status')='verified' AND json_extract(data,'$.payload.result.observation_id')=? AND json_extract(data,'$.payload.result.observation_run')=? ORDER BY seq DESC LIMIT 1",
    runId,
    actionId,
    afterId,
    afterRun,
  );
  if (!before || !after || !dispatch || dispatches.length !== 1 || !settled)
    return unknown('incomplete-execution-evidence');
  if (
    beforeId === afterId ||
    integer(before['seq']) >= integer(dispatch['seq']) ||
    integer(dispatch['seq']) > integer(binding['seq']) ||
    integer(binding['seq']) >= integer(after['seq']) ||
    integer(after['seq']) >= integer(settled['seq'])
  )
    return unknown('invalid-evidence-order');
  const beforeEvent = decode('trace', parsed(before['data']));
  const afterEvent = decode('trace', parsed(after['data']));
  const input = within(store.runDirectory(runId), text(details['input_ref']));
  if (!existsSync(input)) return unknown('missing-action-input');
  let request: ContractMap['device-execute-request'];
  try {
    const value: unknown = JSON.parse(readFileSync(input, 'utf8'));
    request = decode('device-execute-request', value);
  } catch {
    return unknown('unreadable-action-input');
  }
  const actionType = bound.data.kind === 'scroll' ? 'swipe' : bound.data.kind;
  const [left, top, right, bottom] = bound.data.bounds;
  if (
    request.action.type !== actionType ||
    (actionType !== 'back' &&
      !(
        left <= request.action.x &&
        request.action.x < right &&
        top <= request.action.y &&
        request.action.y < bottom
      )) ||
    resolve(request.observation_path) !==
      within(store.runDirectory(runId), text(beforeEvent.payload['evidence_ref']))
  )
    return unknown('action-does-not-match-current-element');
  if (
    !existsSync(within(store.runDirectory(runId), text(beforeEvent.payload['evidence_ref']))) ||
    !existsSync(within(store.runDirectory(afterRun), text(afterEvent.payload['evidence_ref'])))
  )
    return unknown('missing-observation-artifact');
  const settledEvent = decode('trace', parsed(settled['data']));
  const proof: Proof = {
    action_id: actionId,
    action_run_id: runId,
    binding_event_id: bindingEvent.event_id,
    before_observation_id: beforeId,
    after_run_id: afterRun,
    after_observation_id: afterId,
    outcome_event_id: settledEvent.event_id,
  };
  const result: Verification = { status: 'verified', proof };
  return result;
}
