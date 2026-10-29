import { decode, object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Store } from '../storage/store.js';
import type { Fields } from '../storage/values.js';
import type { Decision, Progress } from './types.js';

export class ProgressStore {
  readonly key: string;
  readonly data: Progress;
  private readonly catalogMode: boolean;

  constructor(
    readonly store: Store,
    readonly runId: string,
    task: Fields,
    revision: string,
  ) {
    this.catalogMode = task['goal'] === 'explore';
    this.key = [
      store.run(runId)['task_id'],
      task['instance_id'],
      object(task['metadata'])['sha256'],
      task['credential_profile'],
    ]
      .map(text)
      .join(':');
    const previous = store.progress(this.key);
    const initial = previous
      ? object(previous['data'])
      : {
          identity: null,
          checkpoint: null,
          pending: null,
          paths: [],
          sensitive_regions: [],
          recovery_rounds: 0,
        };
    if (initial['credential_revision'] != null && initial['credential_revision'] !== revision)
      throw new FrameworkError(
        ErrorCode.CREDENTIAL_UNAVAILABLE,
        'Credential profile changed; use a new profile reference',
      );
    initial['credential_revision'] = revision;
    initial['goals'] ??= {};
    initial['milestones'] ??= {};
    if (initial['identity'])
      initial['identity'] = {
        small_account: null,
        server: null,
        role: null,
        ...object(initial['identity']),
      };
    this.data = decode('gameplay-progress', initial);
    if (previous) {
      const payload = {
        previous_run: previous['run_id'],
        identity: this.data.identity,
        checkpoint: this.data.checkpoint,
        server_state_requires_observation: true,
      };
      store.event(runId, 'gameplay.progress.restored', payload);
    }
  }

  update(decision: Decision, observationId: string): void {
    const recovery = decision.recovery;
    if (recovery && !this.catalogMode) {
      const attempts = this.data.recovery_attempts ?? {};
      if (recovery.phase === 'begin') {
        const count = (attempts[recovery.fault] ?? 0) + 1;
        attempts[recovery.fault] = count;
        if (count > 2)
          throw new FrameworkError(
            ErrorCode.GAMEPLAY_BLOCKED,
            'Automatic recovery limit reached for this fault',
          );
      }
      this.data.recovery_attempts = attempts;
      this.store.event(this.runId, 'gameplay.recovery', recovery);
    }
    const goals = { ...this.data.goals };
    const milestones = { ...this.data.milestones };
    for (const proposal of decision.subgoals ?? []) {
      const known = [goals[proposal.id], milestones[proposal.id]];
      const references = new Set(
        known.flatMap((record) => (record?.status === 'verified' ? record.evidence_refs : [])),
      );
      if (
        proposal.status === 'verified' &&
        !proposal.evidence_refs.every((ref) => references.has(ref)) &&
        !proposal.evidence_refs.includes(observationId)
      )
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Subgoal proof is missing');
      goals[proposal.id] = proposal;
    }
    if (Object.keys(goals).length > 100)
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Subgoal queue requires a new scope');
    for (const goal of Object.values(goals)) {
      const visited = new Set<string>();
      const pending = [...goal.requires];
      while (pending.length) {
        const dependency = pending.pop();
        if (
          !dependency ||
          dependency === goal.id ||
          (!goals[dependency] && !milestones[dependency])
        )
          throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Invalid subgoal dependency');
        if (!visited.has(dependency)) {
          pending.push(...(goals[dependency]?.requires ?? []));
          visited.add(dependency);
        }
      }
      if (
        goal.status === 'active' &&
        goal.requires.some((key) => (goals[key] ?? milestones[key])?.status !== 'verified')
      )
        throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Subgoal prerequisites are unmet');
    }
    for (const proposal of decision.progress_update ?? []) {
      if (!proposal.evidence_refs.includes(observationId)) {
        if (this.catalogMode) {
          this.store.event(this.runId, 'gameplay.progress.rejected', {
            id: proposal.id,
            reason: 'missing-current-observation',
            code: ErrorCode.AI_OUTPUT_INVALID,
          });
          continue;
        }
        throw new FrameworkError(
          ErrorCode.AI_OUTPUT_INVALID,
          'Progress update must cite the current observation',
        );
      }
      milestones[proposal.id] = { ...proposal, run_id: this.runId };
    }
    if (Object.keys(milestones).length > 500)
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Milestone scope is full');
    this.data.goals = goals;
    this.data.milestones = milestones;
  }

  save(): void {
    this.data.paths = this.data.paths.slice(-50);
    const request = {
      run_id: this.runId,
      workspace_key: this.key,
      data: decode('gameplay-progress', this.data),
    };
    this.store.command('checkpoint', request);
  }
}
