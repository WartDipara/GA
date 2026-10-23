import { performance } from 'node:perf_hooks';
import { decode } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { canonical, within } from '../storage/values.js';
import type { Runtime } from './runtime.js';
import type { Decision, Facts, Observation, Routine } from './types.js';

export type View = { observation: Observation; facts: Facts };

export class Perception {
  constructor(readonly runtime: Runtime) {}

  async analyze(observation: Observation, previous: Observation | null = null): Promise<Facts> {
    const key = `${observation.observation_id}:${previous?.observation_id ?? ''}`;
    const cached = this.runtime.cache.get(key);
    if (cached) {
      this.runtime.record('gameplay.perception.reused', {
        observation_id: observation.observation_id,
        source_event_id: cached.event,
      });
      return cached.facts;
    }
    const request = {
      image_path: within(this.runtime.directory, observation.evidence_ref),
      previous_image_path: previous ? within(this.runtime.directory, previous.evidence_ref) : null,
      controls: observation.controls,
      excluded_regions: observation.sensitive_regions,
    };
    const facts = decode(
      'perception-result',
      await this.runtime.call('perception.analyze', request),
    );
    const event = this.runtime.record('gameplay.perception', {
      observation_id: observation.observation_id,
      previous: previous?.observation_id ?? null,
      ...facts,
    });
    this.runtime.cache.set(key, { facts, event });
    const oldest = this.runtime.cache.keys().next().value;
    if (this.runtime.cache.size > 4 && oldest) this.runtime.cache.delete(oldest);
    return facts;
  }

  async settle(before: Observation): Promise<View> {
    const started = performance.now();
    for (let attempt = 0; ; attempt += 1) {
      await this.runtime.pause(500);
      const observation = await this.runtime.observe();
      const facts = await this.analyze(observation, before);
      if (facts.stable || attempt === 4) {
        this.runtime.record('gameplay.wait', {
          duration_ms: Math.round(performance.now() - started),
          stable: facts.stable,
        });
        const view = { observation, facts };
        return view;
      }
      before = observation;
    }
  }

  async wait(before: Observation, minimumSeconds = 0): Promise<View> {
    const started = performance.now();
    for (;;) {
      await this.runtime.pause(2000);
      const observation = await this.runtime.observe();
      const facts = await this.analyze(observation, before);
      const changed =
        canonical(observation.runtime.focused_display) !==
          canonical(before.runtime.focused_display) ||
        observation.runtime.alive !== before.runtime.alive ||
        (facts.difference !== null && facts.difference >= 0.08);
      const elapsed = performance.now() - started;
      if ((changed && elapsed >= minimumSeconds * 1000) || elapsed >= 90000) {
        this.runtime.record('gameplay.wait', {
          duration_ms: Math.round(elapsed),
          reason: 'known-transition',
          changed,
          ai_calls: 0,
        });
        const view = { observation, facts };
        return view;
      }
    }
  }
}

function matches(
  condition: Decision['local_after'][number],
  observation: Observation,
  facts: Facts,
): boolean {
  switch (condition.kind) {
    case 'screen':
      return condition.value === facts.screen_key;
    case 'image':
      return condition.value === observation.screenshot.sha256;
    case 'control':
      return facts.candidates.some((candidate) => candidate.target_ref === condition.value);
    case 'focused_package':
      return observation.runtime.focused_display?.package_name === condition.value;
    case 'alive':
      return observation.runtime.alive === (condition.value === 'true');
  }
}

export function route(
  view: View,
  step: Routine['steps'][number] | null = null,
): { route: string; reason: string } {
  let route = 'plan';
  let reason = 'unknown-state-or-new-goal';
  if (!view.observation.runtime.focused_display) {
    route = 'wait';
    reason = 'display-transition';
  } else if (step) {
    const ready =
      view.facts.stable &&
      step.before.some((condition) => condition.kind === 'screen') &&
      step.before.every((condition) => matches(condition, view.observation, view.facts));
    route = ready ? 'routine' : 'plan';
    reason = ready ? 'verified-preconditions' : 'routine-precondition-mismatch';
  }
  const result = { route, reason };
  return result;
}

export async function executeRoutine(
  runtime: Runtime,
  initial: Observation,
  steps: Routine['steps'][number][],
  facts?: Facts,
): Promise<View> {
  if (steps.length < 1 || steps.length > 5)
    throw new FrameworkError(ErrorCode.INVALID_CONTRACT, 'Routines require one to five steps');
  runtime.begin('guarded-routine');
  const perception = new Perception(runtime);
  try {
    let view = facts ? { observation: initial, facts } : await perception.settle(initial);
    for (const step of steps) {
      const selected = route(view, step);
      runtime.record('gameplay.route', selected);
      if (selected.route !== 'routine')
        throw new FrameworkError(
          ErrorCode.GAMEPLAY_BLOCKED,
          'Routine conditions changed; planning is required',
        );
      const actionId = await runtime.perform(step.action, view.observation, 'routine');
      const after = await perception.settle(view.observation);
      const status = !step.after.length
        ? 'unknown'
        : view.observation.runtime.boot_id !== after.observation.runtime.boot_id
          ? 'deviated'
          : step.after.every((condition) => matches(condition, after.observation, after.facts))
            ? 'verified'
            : after.facts.stable
              ? 'deviated'
              : 'unknown';
      const result = {
        status,
        observation_id: after.observation.observation_id,
        before_observation_id: view.observation.observation_id,
        source: 'local-predicates',
        business_goal_verified: false,
      };
      runtime.record('gameplay.outcome', result);
      if (status !== 'verified')
        throw new FrameworkError(
          ErrorCode.GAMEPLAY_BLOCKED,
          'Routine stopped because the expected result is unverified',
        );
      runtime.outcome(runtime.runId, actionId, 'verified', result);
      runtime.progress.data.pending = null;
      runtime.checkpoint(after.observation);
      view = after;
    }
    runtime.finish();
    return view;
  } catch (error) {
    runtime.finish(error);
    throw error;
  }
}
