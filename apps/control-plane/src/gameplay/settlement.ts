import { decode, object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Assessment } from './agents.js';
import type { View } from './local.js';
import type { Runtime } from './runtime.js';
import type { Pending } from './types.js';

type RecordedAssessment = Assessment & { observation_id: string; observation_run: string };

export function readSettlement(runtime: Runtime, pending: Pending): RecordedAssessment | null {
  if (!pending.action_id) return null;
  if (runtime.store.run(pending.run_id)['task_id'] !== runtime.store.run(runtime.runId)['task_id'])
    throw new FrameworkError(ErrorCode.INVALID_PATH, 'Action receipt belongs to a different task');
  const row = runtime.store.deviceAction(pending.run_id, pending.action_id);
  if (!row || (row['status'] !== 'verified' && row['status'] !== 'reconciled')) return null;
  const result = object(row['result'] ?? {});
  const status = result['status'];
  if (status !== 'verified' && status !== 'deviated') return null;
  const verdict = decode('test-evidence-verdict', {
    status,
    reason: result['reason'],
    evidence_refs: result['evidence_refs'],
    missing_evidence: result['missing_evidence'],
    delegation: null,
    context_request: null,
    context_sequence: 0,
  });
  return {
    status,
    observation_id: text(result['observation_id']),
    observation_run: text(result['observation_run']),
    reason: verdict.reason,
    evidence_refs: verdict.evidence_refs,
    missing_evidence: verdict.missing_evidence,
  };
}

export function settleAction(
  runtime: Runtime, pending: Pending, view: View, assessment: Assessment, source: string,
): void {
  if (assessment.status === 'unknown') return;
  const { progress } = runtime;
  const actionId = text(pending.action_id);
  const observationId = view.observation.observation_id;
  const { status } = assessment;
  const outcome = {
    source, action_id: actionId, observation_run: runtime.runId,
    observation_id: observationId, ...assessment,
  };
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
    progress.save();
  });
}
