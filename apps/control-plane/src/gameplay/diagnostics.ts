import { setTimeout as delay } from 'node:timers/promises';
import { decode } from '@game-automation/contracts';
import { frameworkError, type FrameworkError } from '@game-automation/contracts/errors';
import type { Runtime } from './runtime.js';

export class Diagnostics {
  private pending: Promise<void> | null = null;
  private active = false;
  private readonly cancellation = new AbortController();
  private windows = new Map<string, string[]>();
  private failure: FrameworkError | undefined;

  constructor(private readonly runtime: Runtime) {}

  request(actionId: string | null = null): void {
    this.runtime.check();
    if (this.failure) throw this.failure;
    if (this.active) {
      this.runtime.record('gameplay.logs.skipped', {
        reason: 'collection-in-progress',
        action_id: actionId,
      });
      return;
    }
    this.active = true;
    this.pending = this.collect(actionId, this.runtime.step)
      .catch((error) => {
        this.failure = frameworkError(error);
      })
      .finally(() => {
        this.active = false;
      });
  }

  private async collect(actionId: string | null, parent: string | null): Promise<void> {
    const runtime = this.runtime;
    const request = {
      session: runtime.session,
      package_name: runtime.task['target'],
      credential_path: runtime.credentialPath,
    };
    try {
      const signal = AbortSignal.any([runtime.execution.signal, this.cancellation.signal]);
      const result = decode(
        'device-logs-result',
        await runtime.host.execute(
          'call',
          request,
          { capability: 'device.logs', parent_span_id: parent },
          signal,
        ),
      );
      const current = new Map<string, string[]>();
      const windows = result.windows.map((window) => {
        const process = window.process;
        const key = JSON.stringify([result.boot_id, process.uid, process.pid, process.start_ticks]);
        const lines = window.text.split(/\r?\n/);
        if (lines.at(-1) === '') lines.pop();
        const previous = this.windows.get(key) ?? [];
        let overlap = Math.min(previous.length, lines.length);
        while (
          overlap > 0 &&
          !previous.slice(-overlap).every((line, index) => line === lines[index])
        )
          overlap -= 1;
        current.set(key, lines);
        const output = {
          ...window,
          text: lines.slice(overlap).join('\n'),
          possible_gap: previous.length > 0 && lines.length > 0 && overlap === 0,
        };
        return output;
      });
      this.windows = current;
      const payload = {
        ...result,
        windows,
        action_id: actionId,
        coverage: 'bounded snapshots; intervening log loss is possible',
      };
      runtime.record('gameplay.logs', payload, parent);
    } catch (error) {
      if (runtime.host.failure || runtime.store.database.failure) throw error;
      runtime.record(
        'gameplay.logs.unavailable',
        { action_id: actionId, error: frameworkError(error).details() },
        parent,
      );
    }
  }

  async close(): Promise<void> {
    if (this.pending) {
      const timer = new AbortController();
      const completed = await Promise.race([
        this.pending.then(() => true),
        delay(5000, false, { signal: timer.signal }).catch(() => true),
      ]);
      timer.abort();
      if (!completed) this.cancellation.abort();
      await this.pending;
      if (!completed)
        this.runtime.record('gameplay.logs.incomplete', { reason: 'finalization-deadline' });
    }
    if (this.failure) throw this.failure;
  }
}
