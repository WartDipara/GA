import { checkpointHash } from '../utilities/hashing.js';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { TestAgents } from './agents.js';
import type { Perception, View } from './local.js';
import type { Runtime } from './runtime.js';
import type { Decision, Observation, Progress } from './types.js';

const minimumWaitMs = 2000;
const maximumUpdateWaitMs = 60000;
const networkRechecks = [3000, 10000];
const byteUnits: Record<string, number> = { B: 1, KB: 1000, MB: 1000 ** 2, GB: 1000 ** 3 };
type UpdateSample = NonNullable<Progress['update_wait']>;

function localProgress(observation: Observation): Pick<UpdateSample, 'unit' | 'completed' | 'total'> | null {
  const found = new Map<string, Pick<UpdateSample, 'unit' | 'completed' | 'total'>>();
  for (const control of observation.controls) {
    if (control.sensitive) continue;
    const label = control.label.normalize('NFKC').trim();
    const percent = /(?:^|\s)(\d{1,3}(?:\.\d+)?)\s*%$/.exec(label);
    const size = /(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)\s*\/\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)$/i.exec(label);
    if (percent && Number(percent[1]) <= 100) {
      const completed = Number(percent[1]);
      found.set(`percent:${completed}`, { unit: 'percent', completed, total: 100 });
    } else if (size) {
      const completed = Number(size[1]) * (byteUnits[size[2]?.toUpperCase() ?? ''] ?? 0);
      const total = Number(size[3]) * (byteUnits[size[4]?.toUpperCase() ?? ''] ?? 0);
      if (Number.isFinite(completed) && Number.isFinite(total) && total > 0 && completed <= total)
        found.set(`bytes:${completed}:${total}`, { unit: 'bytes', completed, total });
    }
  }
  return found.size === 1 ? found.values().next().value ?? null : null;
}

export class PassiveWait {
  constructor(private readonly runtime: Runtime, private readonly perception: Perception) {}

  async evaluate(decision: Decision, view: View, agents: TestAgents): Promise<View | null> {
    const progress = this.runtime.progress.data;
    const { observation } = view;
    const { update_progress: update, network_issue: issue } = decision;
    if (update && issue)
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'A passive state requires one observed cause');
    if (update && (decision.disposition !== 'wait' || update.observation_id !== observation.observation_id))
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Update waiting requires current evidence');
    if (issue && (!['wait', 'blocked'].includes(decision.disposition) ||
        issue.observation_id !== observation.observation_id))
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Network triage requires current evidence');
    if (issue || progress.network_wait) return this.network(decision, view, agents);
    if (update) return this.update(update, view);
    if (progress.update_wait) {
      delete progress.update_wait;
      this.runtime.progress.save();
    }
    return null;
  }

  private async update(update: NonNullable<Decision['update_progress']>, view: View): Promise<View> {
    const runtime = this.runtime;
    const progress = runtime.progress.data;
    const { observation } = view;
    const live = observation.runtime;
    if (!live.alive || live.focused_display?.package_name !== runtime.task['target'])
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Update evidence is outside the running target');
    const main = live.main_process;
    const process = checkpointHash([live.boot_id, main?.pid, main?.start_ticks]);
    const candidate = localProgress(observation);
    const observed = candidate?.unit === update.unit && candidate.completed === update.completed &&
      candidate.total === update.total ? candidate : null;
    const reading = observed ?? update;
    const { unit, total, completed } = reading;
    const numeric = unit !== 'unknown' && completed !== null && total !== null && total > 0 &&
      completed <= total && (unit !== 'percent' || total === 100);
    const now = observation.observed_at_ms;
    const previous = progress.update_wait;
    const sameStage = previous?.stage === update.stage && previous.process === process;
    let eta: number | null = null;
    const prior = previous?.completed;
    if (numeric && sameStage && previous.unit === unit && previous.total === total &&
        prior != null && completed > prior && now > previous.sampled_at_ms)
      eta = Math.ceil((total - completed) * (now - previous.sampled_at_ms) / (completed - prior));
    if (eta !== null && !Number.isFinite(eta)) eta = null;
    const unchanged = previous && eta === null ? previous.unchanged + 1 : 0;
    if (unchanged >= 8) {
      runtime.record('gameplay.update.stalled', { observation_id: observation.observation_id, previous });
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Update progress could not be verified after bounded rechecks');
    }
    const sample: UpdateSample = {
      stage: update.stage, process, unit: numeric ? unit : 'unknown',
      completed: numeric ? completed : null, total: numeric ? total : null,
      observation_id: observation.observation_id, sampled_at_ms: now, unchanged,
      measurement: observed && main ? 'android-controls' : 'lead-observation',
    };
    runtime.store.database.transaction(() => {
      progress.update_wait = sample;
      if (eta !== null && sample.measurement === 'android-controls' &&
          previous?.measurement === 'android-controls') {
        const proof = checkpointHash(['update', process, update.stage, unit, completed, total]);
        runtime.watchdog?.observeProgress(proof);
        runtime.record('gameplay.update.progress', {
          proof, previous_observation_id: previous.observation_id,
          observation_id: observation.observation_id, completed, total, unit,
        });
      }
      runtime.progress.save();
    });
    const planned = eta ?? minimumWaitMs * 2 ** Math.min(unchanged, 4);
    return this.wait(view, 'gameplay.update', planned, maximumUpdateWaitMs, {
      sample, previous_observation_id: previous?.observation_id ?? null,
      estimated_remaining_ms: eta, measurement: sample.measurement,
    });
  }

  private async network(decision: Decision, view: View, agents: TestAgents): Promise<View | null> {
    const runtime = this.runtime;
    const progress = runtime.progress.data;
    const previous = progress.network_wait;
    const issue = decision.network_issue;
    const observationId = view.observation.observation_id;
    if (!issue && previous) {
      const verdict = await agents.judge(
        view,
        'Check whether the previous connection error recovered. Require a usable next game screen or an explicit successful connection; disappearance of the message alone is insufficient.',
        { observation_id: observationId, previous: previous.observation_id },
        null, [previous.observation_id],
      );
      if (verdict.status === 'verified' && verdict.evidence_refs.includes(previous.observation_id)) {
        runtime.store.database.transaction(() => {
          runtime.record('gameplay.network.classified', { classification: 'recovered', verdict });
          delete progress.network_wait;
          runtime.progress.save();
        });
        // A fresh main decision sees the verified recovery before proposing input.
        return view;
      }
    }
    if (issue?.kind === 'explicit-installation-error') {
      const verdict = await agents.judge(
        view,
        'Verify a visible, specific package, installation, build, or client configuration rejection. A generic network error or repeated failure is insufficient.',
        { observation_id: observationId, message: issue.message },
      );
      if (verdict.status === 'verified') {
        runtime.record('gameplay.network.classified', { classification: 'explicit-client-rejection', issue, verdict });
        throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'The game reported an explicit client rejection');
      }
    }
    const count = previous?.rechecks ?? 0;
    const interval = networkRechecks[count];
    if (interval === undefined) {
      runtime.record('gameplay.network.classified', {
        classification: 'unknown', observation_id: observationId, issue: issue ?? null,
        previous_observation_id: previous?.observation_id, rechecks: count,
      });
      throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED, 'Connection failure persisted or recovery could not be verified');
    }
    if (!previous) {
      progress.network_wait = { observation_id: observationId, rechecks: 0 };
      runtime.progress.save();
    }
    const next = await this.wait(view, 'gameplay.network', interval, interval, {
      issue: issue ?? null, recheck: count + 1,
    });
    progress.network_wait = {
      observation_id: previous?.observation_id ?? observationId, rechecks: count + 1,
    };
    runtime.progress.save();
    return next;
  }

  private async wait(
    view: View, event: string, plannedMs: number, limitMs: number, evidence: Record<string, unknown>,
  ): Promise<View> {
    const runtime = this.runtime;
    const requested = Math.min(limitMs, Math.max(minimumWaitMs, plannedMs));
    const remaining = runtime.execution.deadline.remaining();
    const duration = remaining === null ? requested : Math.min(requested, Math.floor(remaining));
    const observationId = view.observation.observation_id;
    runtime.record(`${event}.wait`, { observation_id: observationId, duration_ms: duration, ...evidence });
    const start = performance.now();
    let resampled: string | null = null;
    try {
      await runtime.pause(duration);
      const observation = await runtime.observe();
      const facts = await this.perception.analyze(observation, view.observation);
      resampled = observation.observation_id;
      return { observation, facts };
    } finally {
      runtime.record(`${event}.wait.finished`, {
        observation_id: observationId, resampled_observation_id: resampled,
        duration_ms: Math.round(performance.now() - start),
        deadline_elapsed: runtime.execution.deadline.expired(),
      });
    }
  }
}
