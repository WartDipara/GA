import { checkpointHash } from '../utilities/hashing.js';
import { performance } from 'node:perf_hooks';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { integer, type Fields } from '../utilities/values.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation, Pending, Progress } from './types.js';
import type { View } from './local.js';
import type { Assessment } from './agents.js';

export class Watchdog {
  readonly data: NonNullable<Progress['watchdog']>;
  reconciling: boolean;
  private updated = performance.now();
  private recoveryAssistance = 0;
  constructor(
    readonly runtime: Runtime,
    readonly settings: Fields,
    private readonly redirectStall: (() => boolean) | null = null,
  ) {
    const scope = String(runtime.task['goal']);
    const previous = runtime.progress.data.watchdog;
    this.data =
      previous && (!previous.scope || previous.scope === scope)
        ? previous
        : { rounds: 0, elapsed_seconds: 0, history: [], proofs: [] };
    const screenKey = runtime.progress.data.checkpoint?.['screen_key'];
    if (previous === this.data && typeof screenKey === 'string')
      this.data.screen_key ??= screenKey;
    this.data.scope = scope;
    runtime.progress.data.watchdog = this.data;
    this.reconciling = this.data.blocked === true;
    if (this.reconciling) runtime.record('gameplay.watchdog.reconciliation', {
      scope, pending_action: runtime.progress.data.pending?.action_id ?? null,
    });
  }

  check(): void {
    const current = performance.now();
    this.data.elapsed_seconds += (current - this.updated) / 1000;
    this.updated = current;
    if (this.reconciling) return;
    if (
      this.data.rounds >= integer(this.settings['no_progress_rounds'] ?? 24) ||
      this.data.elapsed_seconds >= integer(this.settings['no_progress_seconds'] ?? 900)
    ) {
      if (!this.runtime.progress.data.pending && this.redirectStall?.()) {
        this.runtime.record('gameplay.watchdog.redirected', {
          reason: 'local-stall-isolated',
          rounds: this.data.rounds,
          elapsed_seconds: this.data.elapsed_seconds,
          verified_progress: false,
        });
        this.data.rounds = 0;
        this.data.elapsed_seconds = 0;
        this.runtime.progress.save();
        return;
      }
      this.block('no-verified-progress');
    }
  }

  assistance(): void {
    const limit = integer(this.settings['no_progress_rounds'] ?? 24);
    if (this.reconciling) {
      this.recoveryAssistance += 1;
      if (this.recoveryAssistance >= limit) this.block('reconciliation-without-new-evidence');
      return;
    }
    this.data.rounds += 1;
    this.runtime.progress.save();
    if (this.data.rounds >= limit) this.block('assistance-without-verified-progress');
    this.check();
  }

  review(
    decision: Decision,
    view: View,
    pending: Pending | null,
    assessment: Assessment | null,
  ): void {
    const { observation, facts } = view;
    this.check();
    this.data.rounds += 1;
    const state = decision.state_key || 'unknown';
    const verified =
      pending &&
      assessment?.status === 'verified' &&
      assessment.evidence_refs.includes(observation.observation_id);
    const previous = this.data.state;
    const exploring = this.runtime.task['goal'] === 'explore';
    if (this.reconciling) {
      const changed = facts.stable && previous && previous !== state && state !== 'unknown' &&
        this.data.screen_key !== undefined && this.data.screen_key !== facts.screen_key;
      if (!verified && !changed && decision.disposition !== 'complete')
        this.block('reconciliation-without-new-evidence');
      if (verified || changed)
        this.observeProgress(checkpointHash(['resume', previous, state, pending?.action_id]));
      this.reconciling = false;
      if (decision.disposition === 'complete') return;
    }
    if (!exploring && verified && previous && state !== previous && state !== 'unknown') {
      const signature = checkpointHash([previous, state, pending.action.type]);
      if (!this.data.proofs.includes(signature)) {
        this.data.proofs = [...this.data.proofs, signature].slice(-256);
        this.data.rounds = 0;
        this.data.elapsed_seconds = 0;
      }
    }
    if (verified && pending.action.type !== 'launch')
      this.runtime.progress.data.recovery_rounds = 0;
    this.data.state = state;
    if (facts.stable) this.data.screen_key = facts.screen_key;
    if (!exploring && decision.action && decision.action.type !== 'wait') {
      const fingerprint = checkpointHash([state, decision.action]);
      this.data.history = [...this.data.history, fingerprint].slice(-16);
      const count = this.data.history.filter((value) => value === fingerprint).length;
      if (count >= 3) this.runtime.record('gameplay.stall', { fingerprint, count });
      if (count >= 5) this.block('repeated-state-and-action-cycle');
    }
    this.check();
    this.data.blocked = false;
    this.runtime.progress.save();
  }

  observeProgress(proof: string): void {
    if (this.data.proofs.includes(proof)) return;
    this.data.proofs = [...this.data.proofs, proof].slice(-256);
    this.data.rounds = 0;
    this.data.elapsed_seconds = 0;
    this.updated = performance.now();
    this.data.history = [];
    this.data.corrections = {};
    this.data.blocked = false;
    this.reconciling = false;
    this.runtime.progress.save();
  }

  withoutDispatch(observation: Observation, reason: string, scope = 'unidentified-page'): void {
    const key = checkpointHash([scope, reason]);
    const corrections = this.data.corrections ?? {};
    const attempts = (corrections[key] ?? 0) + 1;
    corrections[key] = attempts;
    this.data.corrections = corrections;
    this.runtime.record('gameplay.planning.correction', {
      observation_id: observation.observation_id,
      reason,
      scope,
      attempt: attempts,
      maximum: 2,
      dispatched: false,
    });
    this.runtime.progress.save();
    if (attempts >= 2) this.block('planning-correction-without-new-evidence');
  }

  private block(reason: string): never {
    this.data.blocked = true;
    this.runtime.store.database.transaction(() => {
      this.runtime.record('gameplay.watchdog.blocked', { reason, ...this.data });
      if (this.runtime.task['goal'] === 'explore')
        this.runtime.record('exploration.supervision.stopped', {
          reason,
          feedback: this.runtime.progress.data.exploration_supervisor?.feedback ?? null,
          pending_action: this.runtime.progress.data.pending?.action_id ?? null,
          checkpoint: this.runtime.progress.data.checkpoint,
          report_policy: 'publish-after-business-state-settlement',
        });
      this.runtime.progress.save();
    });
    throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, `Progress watchdog: ${reason}`);
  }
}
