import { createHash } from 'node:crypto';
import { decode } from '@game-automation/contracts';
import { canonical } from '../storage/values.js';
import type { Runtime } from './runtime.js';
import type { Facts, Observation, Pending, Progress, Routine } from './types.js';

// Retain existing checkpoint fingerprints across the Python-to-TypeScript migration.
export function checkpointHash(value: unknown): string {
  const format = (item: unknown): string => {
    if (Array.isArray(item)) return `[${item.map(format).join(', ')}]`;
    if (item !== null && typeof item === 'object') {
      const fields = Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
      return `{${fields.map(([key, value]) => `${format(key)}: ${format(value)}`).join(', ')}}`;
    }
    return (JSON.stringify(item) ?? 'null').replace(
      /[\u007f-\uffff]/g,
      (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
    );
  };
  return createHash('sha256').update(format(value)).digest('hex');
}

export class Knowledge {
  readonly entries: NonNullable<Progress['learned_steps']>;
  constructor(readonly runtime: Runtime) {
    this.entries = runtime.progress.data.learned_steps ??= [];
  }

  scope(observation: Observation): string | null {
    const identity = this.runtime.progress.data.identity;
    const display = observation.runtime.focused_display;
    if (!identity || !display) return null;
    const main = observation.runtime.main_process;
    const process = main ? { pid: main.pid, uid: main.uid, start_ticks: main.start_ticks } : null;
    const value = {
      apk: this.runtime.task['metadata'],
      identity,
      boot_id: observation.runtime.boot_id,
      display,
      process,
    };
    const metadata = decode('apk-metadata', value.apk);
    return checkpointHash({ ...value, apk: metadata.sha256 });
  }

  intent(): string | null {
    const active = Object.values(this.runtime.progress.data.goals).filter(
      (goal) => goal.status === 'active',
    );
    if (active.length === 1) return active[0]?.id ?? null;
    return this.runtime.task['goal'] === 'login-and-interact' ? 'login-and-interact' : null;
  }

  learn(pending: Pending, observation: Observation, facts: Facts): void {
    if (
      pending.risk !== 'none' ||
      !pending.cache_scope ||
      !['tap', 'back'].includes(pending.action.type) ||
      observation.sensitive_regions.length ||
      !facts.stable ||
      pending.sensitive_regions?.length ||
      !pending.cache_intent ||
      pending.cache_scope !== this.scope(observation) ||
      !pending.image_sha256 ||
      !pending.screen_key ||
      !pending.action_id
    )
      return;
    const entry = {
      scope: pending.cache_scope,
      intent: pending.cache_intent,
      before: pending.image_sha256,
      before_screen: pending.screen_key,
      after: observation.screenshot.sha256,
      after_screen: facts.screen_key,
      action: pending.action,
      source_run: pending.run_id,
      action_id: pending.action_id,
      observation_id: observation.observation_id,
    };
    if (entry.before === entry.after) return;
    this.entries.push(entry);
    this.entries.splice(0, Math.max(0, this.entries.length - 100));
    this.runtime.record('gameplay.cache.learned', entry);
  }

  routine(observation: Observation, facts: Facts): Routine['steps'][number][] {
    const steps: Routine['steps'][number][] = [];
    if (!facts.stable || observation.sensitive_regions.length) return steps;
    const scope = this.scope(observation);
    const intent = this.intent();
    let current = observation.screenshot.sha256;
    const visited = new Set<string>();
    while (steps.length < 5 && !visited.has(current)) {
      const matches = this.entries.filter(
        (entry) => entry.scope === scope && entry.intent === intent && entry.before === current,
      );
      const unique = new Set(matches.map((entry) => canonical(entry.action)));
      const entry = matches.at(-1);
      if (unique.size !== 1 || !entry) break;
      const packageName = observation.runtime.package_name;
      const step: Routine['steps'][number] = {
        before: [
          { kind: 'image', value: current },
          { kind: 'screen', value: entry.before_screen },
          { kind: 'focused_package', value: packageName },
        ],
        action: entry.action,
        after: [
          { kind: 'image', value: entry.after },
          { kind: 'focused_package', value: packageName },
        ],
      };
      steps.push(step);
      visited.add(current);
      current = entry.after;
    }
    return steps;
  }
}
