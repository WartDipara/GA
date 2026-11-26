import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import type { Store } from '../storage/store.js';
import { integer, parsed } from '../utilities/values.js';
import { within } from '../utilities/files.js';

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
  const action = store.actionRecords.forTask(taskId, actionId);
  if (!action || action['status'] !== 'verified') return unknown('action-not-verified');
  const runId = text(action['run_id']);
  const details = parsed(action['result']);
  const outcome = object(details['result']);
  const afterId = outcome['observation_id'];
  const afterRun = outcome['observation_run'];
  if (typeof afterId !== 'string' || typeof afterRun !== 'string')
    return unknown('missing-post-action-observation');
  const binding = store.eventRecords.explorationBinding(runId, workspace, element.id, actionId);
  if (!binding) return unknown('missing-element-action-binding');
  const bindingEvent = decode('trace', parsed(binding['data']));
  const bound = decode('exploration-record', bindingEvent.payload['record']);
  if (bound.kind !== 'element') return unknown('invalid-element-action-binding');
  const beforeId = bound.data.observation_id;
  const before = store.eventRecords.observation(runId, beforeId, taskId);
  const after = store.eventRecords.observation(afterRun, afterId, taskId);
  const dispatches = store.eventRecords.actionDispatches(runId, actionId);
  const dispatch = dispatches[0];
  const settled = store.eventRecords.verifiedAction(runId, actionId, afterId, afterRun);
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
    (request.action.type !== actionType &&
      !(actionType === 'tap' && request.action.type === 'long_press')) ||
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
