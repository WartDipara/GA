import { performance } from 'node:perf_hooks';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { integer, type Fields } from '../storage/values.js';
import { checkpointHash } from './knowledge.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation, Pending, Progress } from './types.js';

export class Watchdog {
  readonly data: NonNullable<Progress['watchdog']>;
  reconciling: boolean;
  private updated = performance.now();
  constructor(
    readonly runtime: Runtime,
    readonly settings: Fields,
  ) {
    const scope = String(runtime.task['goal']);
    const previous = runtime.progress.data.watchdog;
    this.data =
      previous && (!previous.scope || previous.scope === scope)
        ? previous
        : { rounds: 0, elapsed_seconds: 0, history: [], proofs: [] };
    this.data.scope = scope;
    runtime.progress.data.watchdog = this.data;
    this.reconciling = Boolean(this.data.blocked && runtime.progress.data.pending);
    if (this.reconciling) runtime.record('gameplay.watchdog.reconciliation', { scope });
  }

  check(): void {
    const current = performance.now();
    this.data.elapsed_seconds += (current - this.updated) / 1000;
    this.updated = current;
    if (this.reconciling) return;
    if (
      this.data.rounds >= integer(this.settings['no_progress_rounds'] ?? 24) ||
      this.data.elapsed_seconds >= integer(this.settings['no_progress_seconds'] ?? 900)
    )
      this.block('no-verified-progress');
  }

  review(decision: Decision, observation: Observation, pending: Pending | null): void {
    this.check();
    this.data.rounds += 1;
    const state = decision.state_key || 'unknown';
    const assessment = decision.previous_outcome;
    const verified =
      pending &&
      assessment.status === 'verified' &&
      assessment.evidence_refs.includes(observation.observation_id);
    const previous = this.data.state;
    if (verified && previous && state !== previous && state !== 'unknown') {
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
    if (decision.action && decision.action.type !== 'wait') {
      const fingerprint = checkpointHash([state, decision.action]);
      this.data.history = [...this.data.history, fingerprint].slice(-16);
      const count = this.data.history.filter((value) => value === fingerprint).length;
      if (count >= 3) this.runtime.record('gameplay.stall', { fingerprint, count });
      if (count >= 5) this.block('repeated-state-and-action-cycle');
    }
    this.reconciling = false;
    this.check();
    this.data.blocked = false;
    this.runtime.progress.save();
  }

  observeProgress(proof: string): void {
    if (this.data.proofs.includes(proof)) return;
    this.data.proofs = [...this.data.proofs, proof].slice(-256);
    this.data.rounds = 0;
    this.data.elapsed_seconds = 0;
    this.data.history = [];
    this.data.blocked = false;
    this.runtime.progress.save();
  }

  private block(reason: string): never {
    this.data.blocked = true;
    this.runtime.record('gameplay.watchdog.blocked', { reason, ...this.data });
    this.runtime.progress.save();
    throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, `Progress watchdog: ${reason}`);
  }
}
