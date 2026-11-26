import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Fields } from '../utilities/values.js';
import type { Assessment } from './agents.js';
import type { Runtime } from './runtime.js';
import type { Observation, Pending } from './types.js';

type RecordedAssessment = ContractMap['action-settlement'];

export function readSettlement(runtime: Runtime, pending: Pending): RecordedAssessment | null {
  if (!pending.action_id) return null;
  if (runtime.store.run(pending.run_id)['task_id'] !== runtime.store.run(runtime.runId)['task_id'])
    throw new FrameworkError(ErrorCode.INVALID_PATH, 'Action receipt belongs to a different task');
  const row = runtime.store.deviceAction(pending.run_id, pending.action_id);
  if (!row || (row['status'] !== 'verified' && row['status'] !== 'reconciled')) return null;
  const result = object(row['result'] ?? {});
  const status = result['status'];
  if (status !== 'verified' && status !== 'deviated') return null;
  if (result['source'] === 'local-predicates' && result['business_goal_verified'] === false &&
      typeof result['before_observation_id'] === 'string' && result['reason'] === undefined)
    return null;
  const receipt = {
    status,
    observation_id: result['observation_id'],
    observation_run: result['observation_run'],
    reason: result['reason'],
    evidence_refs: result['evidence_refs'],
    missing_evidence: result['missing_evidence'],
  };
  return decode('action-settlement', receipt);
}

export function settleAction(
  runtime: Runtime, pending: Pending, observation: Observation, assessment: Assessment,
  source: string, commitCheckpoint?: true | (() => void), evidence?: Fields,
): void {
  if (assessment.status === 'unknown') return;
  const { progress } = runtime;
  const actionId = text(pending.action_id);
  const observationId = observation.observation_id;
  const { status } = assessment;
  const receipt = decode('action-settlement', {
    observation_run: runtime.runId, observation_id: observationId, ...assessment,
  });
  const outcome = { ...evidence, ...receipt, source, action_id: actionId };
  runtime.store.database.transaction(() => {
    runtime.outcome(pending.run_id, actionId, status === 'verified' ? 'verified' : 'reconciled', outcome);
    runtime.record('gameplay.outcome', outcome);
    if (pending.test_attempt) {
      const attempts = progress.data.test_attempts ??= {};
      attempts[pending.test_attempt] = status;
    }
    const path = progress.data.paths.find((item) => item.action_id === actionId);
    if (path) {
      path.status = status;
      path.after = observationId;
    } else if (pending.test_objective) {
      progress.data.paths.push({
        run_id: pending.run_id, action_id: actionId, before: pending.observation_id,
        after: observationId, objective: pending.test_objective, status, type: pending.action.type,
      });
    }
    if (commitCheckpoint) {
      progress.data.pending = null;
      if (typeof commitCheckpoint === 'function') commitCheckpoint();
      runtime.checkpoint(observation);
    } else progress.save();
  });
}
