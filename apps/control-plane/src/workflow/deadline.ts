import { performance } from 'node:perf_hooks';
import { text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Store } from '../storage/store.js';

export const defaultTaskSeconds = 3600;
export const actionReserveMs = 5000;

export class TaskDeadline {
  private deadline: number | null = null;
  private monotonicEnd = Infinity;
  private timer: NodeJS.Timeout | undefined;
  owner: string | null = null;

  constructor(private readonly store: Store, private readonly controller: AbortController) {}

  restore(runId: string): void {
    this.owner = runId;
    const visited = new Set<string>();
    let source = runId;
    for (;;) {
      if (visited.has(source) || this.store.run(source)['task_id'] !== this.store.run(runId)['task_id'])
        throw new FrameworkError(ErrorCode.INVALID_STATE, 'Invalid deadline continuation');
      visited.add(source);
      const pinned = this.store.events(source, { kinds: ['task.deadline.bound'], limit: 1 }).at(-1);
      if (pinned) {
        const deadline = pinned.payload['deadline_ms'];
        if (typeof deadline !== 'number' || !Number.isSafeInteger(deadline))
          throw new FrameworkError(ErrorCode.INVALID_STATE, 'Invalid recorded task deadline');
        this.bind(runId, deadline, source);
        return;
      }
      const link = this.store.events(source, { kinds: ['run.continued_from'], limit: 1 }).at(-1);
      if (!link) return;
      source = text(link.payload['run_id']);
    }
  }

  start(runId: string, started: number, seconds: unknown): void {
    if (this.deadline !== null) return;
    if (typeof seconds !== 'number' || !Number.isSafeInteger(seconds) || seconds < 1 ||
        seconds > 7 * 24 * 3600)
      throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, 'Task time limit must be between 1 and 604800 seconds');
    this.bind(runId, started + seconds * 1000, runId);
  }

  private bind(runId: string, deadline: number, source: string): void {
    if (source !== runId || !this.store.events(runId, { kinds: ['task.deadline.bound'], limit: 1 }).length)
      this.store.event(runId, 'task.deadline.bound', { deadline_ms: deadline, source_run: source });
    this.deadline = deadline;
    this.monotonicEnd = performance.now() + Math.max(0, deadline - Date.now());
    const remaining = this.remaining();
    if (remaining === 0) this.expire();
    else if (remaining !== null) this.timer = setTimeout(() => this.expire(), Math.ceil(remaining));
  }

  remaining(): number | null {
    if (this.deadline === null) return null;
    return Math.max(0, Math.min(this.deadline - Date.now(), this.monotonicEnd - performance.now()));
  }

  context(): { deadline_ms: number | null; remaining_ms: number | null; dispatch_reserve_ms: number } {
    const remaining = this.remaining();
    return {
      deadline_ms: this.deadline,
      remaining_ms: remaining === null ? null : Math.floor(remaining),
      dispatch_reserve_ms: actionReserveMs,
    };
  }

  expired(): boolean {
    return this.remaining() === 0;
  }

  check(): void {
    if (this.expired()) this.expire();
  }

  private expire(): void {
    this.controller.abort(new FrameworkError(ErrorCode.BUDGET_EXHAUSTED, 'The task deadline has elapsed'));
  }

  dispose(): void {
    clearTimeout(this.timer);
  }
}
