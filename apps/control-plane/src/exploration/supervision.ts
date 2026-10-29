import type { Runtime } from '../gameplay/runtime.js';
import type { Observation, Pending, Progress } from '../gameplay/types.js';
import { object } from '@game-automation/contracts';
import { integer } from '../storage/values.js';
import { Catalog, signature, type Element, type Page } from './catalog.js';

/** Consumes settled action evidence; never dispatches device input. */
export class ExplorationSupervisor {
  constructor(readonly catalog: Catalog) {}

  private get runtime(): Runtime {
    return this.catalog.runtime;
  }

  private state(): NonNullable<Progress['exploration_supervisor']> {
    const progress = this.runtime.progress.data;
    if (progress.exploration_supervisor?.scope !== this.catalog.key)
      progress.exploration_supervisor = { scope: this.catalog.key, history: [], feedback: null };
    return progress.exploration_supervisor;
  }

  review(pending: Pending, page: Page | null, observation: Observation, firstProof: boolean): void {
    const link = pending.exploration;
    if (!link) return;
    const element = this.catalog
      .records()
      .find(
        (record): record is Element => record.kind === 'element' && record.id === link.element_id,
      );
    if (!element) return;
    const state = this.state();
    const prior = element.data.supervision;
    if (prior?.action_id === pending.action_id) return;
    const transition = signature([link.from_page, element.id, pending.action.type, page?.id]);
    state.history = [...state.history, transition].slice(-48);
    const repeats = state.history.filter((entry) => entry === transition).length;
    element.data.supervision = {
      recovery_rounds: prior?.recovery_rounds ?? 0,
      repeats,
      quarantined: prior?.quarantined ?? false,
      reason: firstProof ? 'first-verified-element' : 'no-new-verified-element',
      observation_id: observation.observation_id,
      action_id: pending.action_id ?? null,
    };
    this.runtime.record('exploration.supervision.progress', {
      workspace_key: this.catalog.key,
      element_id: element.id,
      action_id: pending.action_id,
      action_run: pending.run_id,
      observation_id: observation.observation_id,
      from_page: link.from_page,
      to_page: page?.id ?? null,
      transition,
      repeats,
      accepted: firstProof,
      reason: element.data.supervision.reason,
    });
    if (firstProof) {
      state.history = [];
      state.feedback = null;
      this.runtime.watchdog?.observeProgress(`element:${this.catalog.key}:${element.id}`);
    } else if (repeats >= 3) {
      this.intervene(element, observation.observation_id, 'repeated-transition-without-progress');
      state.history = [];
    }
    this.catalog.save([element]);
    this.runtime.progress.save();
  }

  intervene(element: Element, observationId: string, reason: string): void {
    const state = this.state();
    const settings = object(object(this.runtime.manifest['application'])['gameplay']);
    const maximum = Math.max(0, integer(settings['supervisor_recovery_rounds'] ?? 2));
    const supervision = element.data.supervision ?? {
      recovery_rounds: 0,
      repeats: 0,
      quarantined: false,
      reason,
      observation_id: observationId,
      action_id: null,
    };
    if (!supervision.quarantined) {
      if (supervision.recovery_rounds >= maximum) supervision.quarantined = true;
      else supervision.recovery_rounds += 1;
    }
    supervision.reason = reason;
    supervision.observation_id = observationId;
    element.data.supervision = supervision;
    if (supervision.quarantined) element.status = 'limited';
    state.feedback = {
      element_id: element.id,
      reason,
      recovery_round: supervision.recovery_rounds,
      quarantined: supervision.quarantined,
      observation_id: observationId,
    };
    this.runtime.record('exploration.supervision.intervention', {
      workspace_key: this.catalog.key,
      ...state.feedback,
      maximum_recovery_rounds: maximum,
      action_id: supervision.action_id,
      instruction:
        'Choose a different evidence-based route or another pending target. Do not repeat the failed action. Quarantined targets cannot be retried.',
    });
    this.catalog.save([element]);
    this.runtime.progress.save();
  }
}
