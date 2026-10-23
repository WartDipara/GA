import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Runtime } from './runtime.js';
import type { Decision, Observation, Progress } from './types.js';

export class Battle {
  readonly data: NonNullable<Progress['battle']>;
  constructor(readonly runtime: Runtime) {
    this.data = runtime.progress.data.battle ??= {
      phase: 'unknown',
      stage: null,
      settlement: null,
      advances: [],
    };
  }

  update(proposal: Decision['battle_progress'], observation: Observation): void {
    if (!proposal) return;
    const current = observation.observation_id;
    if (!proposal.evidence_refs.includes(current))
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Battle evidence is missing');
    if (proposal.phase === 'active' && !proposal.native_auto_observed)
      throw new FrameworkError(
        ErrorCode.GAMEPLAY_BLOCKED,
        'Native automatic battle is not supported by current evidence',
      );
    if (proposal.phase === 'settlement' && proposal.settlement_result === 'victory') {
      if (!['active', 'settlement'].includes(this.data.phase) || !this.data.stage)
        throw new FrameworkError(
          ErrorCode.GAMEPLAY_BLOCKED,
          'Settlement cannot be attributed to an observed battle',
        );
      this.data.settlement = {
        stage: this.data.stage,
        observation_id: current,
        run_id: this.runtime.runId,
        result: 'victory',
      };
    }
    const settlement = this.data.settlement;
    if (proposal.stage && settlement && proposal.stage !== settlement.stage) {
      const advancement = {
        from_stage: settlement.stage,
        to_stage: proposal.stage,
        settlement,
        observation_id: current,
        source: 'model-semantic-review',
        run_id: this.runtime.runId,
      };
      this.data.advances.push(advancement);
      this.data.settlement = null;
      this.runtime.record('gameplay.battle.advanced', advancement);
    }
    this.data.phase = proposal.phase;
    this.data.stage = proposal.stage || this.data.stage;
    this.runtime.record('gameplay.battle.observed', proposal);
  }

  verified(): boolean {
    return this.data.advances.some((advance) => advance.run_id === this.runtime.runId);
  }
}
