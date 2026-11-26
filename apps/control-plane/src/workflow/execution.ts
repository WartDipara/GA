import { performance } from 'node:perf_hooks';
import { publish } from '../reporting/publication.js';
import { existsSync, statSync } from 'node:fs';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Store } from '../storage/store.js';
import { integer, optionalText, type Fields } from '../utilities/values.js';
import { within } from '../utilities/files.js';
import { requireDiskSpace } from '../utilities/disk.js';
import { actionReserveMs, defaultTaskSeconds, TaskDeadline } from './deadline.js';
import { Scheduler } from './scheduler.js';
import { play } from '../gameplay/play.js';
import { finalizeGames, recoverFinalization } from '../gameplay/finalization.js';
import { blockedErrors, settledStatuses } from './state-policy.js';

export type Tool = (operation: string, runId: string, input: Fields) => Promise<unknown>;
const boundaries: Record<string, string[]> = {
  'execution.segment.started': ['execution.segment.finished', 'execution.segment.incomplete'],
  'call.started': ['call.completed', 'call.failed', 'call.incomplete'],
  'gameplay.agent.started': [
    'gameplay.agent.completed',
    'gameplay.agent.rejected',
    'gameplay.agent.incomplete',
  ],
  'gameplay.delegation.started': [
    'gameplay.delegation.completed',
    'gameplay.delegation.rejected',
    'gameplay.delegation.incomplete',
  ],
  'gameplay.step.started': [
    'gameplay.step.completed',
    'gameplay.step.failed',
    'gameplay.step.incomplete',
  ],
  'workflow.stage.started': [
    'workflow.stage.completed',
    'workflow.stage.failed',
    'workflow.stage.incomplete',
  ],
};

export class Execution {
  readonly deadline: TaskDeadline;
  readonly signal: AbortSignal;
  private reportDeadline: number | null = null;

  constructor(
    readonly store: Store,
    readonly tool: Tool,
    controller: AbortController,
    private readonly drain: () => Promise<void>,
  ) {
    this.signal = controller.signal;
    this.deadline = new TaskDeadline(store, controller);
  }

  check(runId: string): void {
    this.deadline.check();
    if (this.signal.aborted)
      throw this.signal.reason instanceof FrameworkError
        ? this.signal.reason
        : new FrameworkError(ErrorCode.CANCELLED);
    if (this.store.database.failure) throw this.store.database.failure;
    this.store.checkCleanup();
    const task = this.store.input(runId);
    const parent = optionalText(task['workflow_run']);
    if (
      this.store.run(runId)['cancel_requested'] ||
      (parent && this.store.run(parent)['cancel_requested'])
    )
      throw new FrameworkError(ErrorCode.CANCELLED);
  }

  checkAction(runId: string): void {
    this.check(runId);
    const remaining = this.deadline.remaining();
    if (remaining !== null && remaining < actionReserveMs) {
      this.store.event(runId, 'task.deadline.closing', this.deadline.context());
      throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED, 'Closing the task before its deadline');
    }
  }

  async manifest(runId: string): Promise<Fields> {
    return object(await this.tool('load', runId, {}));
  }

  async create(
    taskId: string,
    mode: string,
    spec: Fields | null = null,
    parentRun: string | null = null,
  ): Promise<string> {
    this.store.checkCleanup();
    const runId = text(this.store.command('create_run', { task_id: taskId, mode, spec }));
    try {
      await this.tool('freeze', runId, { parent_run: parentRun });
    } catch (error) {
      const failure =
        error instanceof FrameworkError
          ? error
          : new FrameworkError(ErrorCode.SNAPSHOT_MISSING, undefined, false, { cause: error });
      this.store.command('transition', {
        run_id: runId,
        status: failure.code === ErrorCode.CANCELLED ? 'cancelled' : 'blocked',
        reason: failure.details(),
      });
      if (!parentRun) await this.publish(runId);
      if (this.signal.aborted || failure.code === ErrorCode.CANCELLED) throw failure;
    }
    return runId;
  }

  async recover(): Promise<void> {
    await this.drain();
    const runs = this.store.workflows.running();
    for (const row of runs) {
      const runId = text(row['id']);
      const closed = new Set(
        this.store
          .events(runId, { kinds: Object.values(boundaries).flat() })
          .map((event) => event.span_id),
      );
      for (const event of this.store.events(runId, { kinds: Object.keys(boundaries) })) {
        if (closed.has(event.span_id)) continue;
        const payload = {
          ...event.payload,
          error: { code: ErrorCode.PREVIOUS_PROCESS_EXITED },
          status: 'unknown',
          duration_ms: null,
        };
        this.store.event(runId, event.kind.replace(/started$/, 'incomplete'), payload, {
          span_id: event.span_id,
          parent_span_id: event.parent_span_id,
          request_id: event.request_id,
        });
      }
      this.store.command('transition', {
        run_id: runId,
        status: 'blocked',
        reason: { code: ErrorCode.PREVIOUS_PROCESS_EXITED },
      });
    }
    recoverFinalization(this);
  }

  async repairReports(): Promise<unknown[]> {
    const repaired: unknown[] = [];
    const rows = this.store.eventRecords.reportRuns();
    for (const row of rows) {
      if (this.reportingTimeLeft() <= 0) break;
      const runId = text(row['run_id']);
      if (!settledStatuses.has(text(this.store.run(runId)['status']))) continue;
      const request = this.store.events(runId, { kinds: ['report.requested'], limit: 1 }).at(-1);
      const finished = this.store.events(runId, { kinds: ['report.finished'], limit: 1 }).at(-1);
      let complete =
        !!finished &&
        !!request &&
        finished.payload['request_id'] === request.payload['request_id'] &&
        finished.payload['status'] === 'complete';
      if (complete) {
        try {
          const files = finished?.payload['files'];
          if (
            !Array.isArray(files) ||
            !existsSync(within(this.store.runDirectory(runId), 'reports/publication.json'))
          )
            complete = false;
          else
            for (const value of files) {
              const entry = object(value);
              if (
                statSync(within(this.store.runDirectory(runId), text(entry['ref']))).size !==
                integer(entry['bytes'])
              )
                complete = false;
            }
          const taskDirectory = within(
            this.store.root,
            `var/tasks/${text(this.store.run(runId)['task_id'])}`,
          );
          for (const reference of ['reports/task.json', 'reports/task.html', 'apks/index.json']) {
            if (!existsSync(within(taskDirectory, reference))) complete = false;
          }
        } catch {
          complete = false;
        }
      }
      if (!complete) repaired.push(await this.publish(runId));
    }
    return repaired;
  }

  private reportingTimeLeft(): number {
    const now = performance.now();
    this.reportDeadline ??= now + 120_000;
    return Math.floor(this.reportDeadline - now);
  }

  async render(runId: string, component: string): Promise<unknown> {
    const timeout = Math.min(30_000, this.reportingTimeLeft());
    if (timeout <= 0)
      throw new FrameworkError(ErrorCode.TIMEOUT, 'Report publication budget exhausted');
    const input = { component, timeout_ms: timeout };
    return this.tool('render', runId, input);
  }

  async publish(runId: string): Promise<Fields> {
    return publish(this, runId);
  }

  async run(runId: string, sourceFile: string | null): Promise<Fields> {
    this.store.checkCleanup();
    const run = this.store.run(runId);
    const mode = text(run['mode']);
    if (['succeeded', 'failed', 'cancelled'].includes(text(run['status'])))
      throw new FrameworkError(ErrorCode.RUN_TERMINAL);
    const started = Date.now();
    const ownsDeadline = ['workflow', 'gameplay'].includes(mode) && !this.store.input(runId)['workflow_run'];
    this.store.beginSegment(runId);
    let loadedManifest: Fields | null = null;
    try {
      if (
        ![
          'intake',
          'preprocessing',
          'emulators',
          'device',
          'observation',
          'gameplay',
          'workflow',
          'analysis',
        ].includes(mode)
      )
        throw new FrameworkError(ErrorCode.RUN_MODE_REMOVED);
      if (ownsDeadline) {
        this.deadline.restore(runId);
        const snapshot = this.store.events(runId, { kinds: ['run.snapshot'], limit: 1 }).at(-1);
        this.deadline.start(runId, started, snapshot?.payload['task_timeout_seconds'] ?? defaultTaskSeconds);
      }
      this.check(runId);
      const manifest = await this.manifest(runId);
      loadedManifest = manifest;
      const application = object(manifest['application']);
      this.check(runId);
      const unknown = this.store.actionRecords.hasUnknown(runId);
      if (!['device', 'gameplay'].includes(mode) && unknown)
        throw new FrameworkError(ErrorCode.ACTION_ALREADY_DISPATCHED);
      this.store.command('transition', { run_id: runId, status: 'running' });
      const storage = object(application['storage']);
      const reserve = integer(storage['reserve_bytes']);
      const overhead = integer(storage['overhead_bytes_per_apk']);
      const margin = integer(storage['safety_margin_percent']);
      if (reserve < 1 || overhead < 1 || margin < 1 || margin > 100)
        throw new FrameworkError(ErrorCode.INVALID_STORAGE_POLICY);
      requireDiskSpace(this.store.runDirectory(runId), reserve);
      const task = this.store.input(runId);
      let timeout = 0;
      if (mode === 'preprocessing')
        timeout = integer(object(application['preprocessing'])['batch_timeout_seconds']);
      else if (mode === 'intake')
        timeout = integer(object(application['intake'] ?? {})['timeout_seconds'] ?? 300);
      const stages: Record<string, string> = {
        preprocessing: 'preprocessing',
        emulators: 'environment',
        device: 'environment',
        observation: 'environment',
        gameplay: 'exploration',
        analysis: 'observability',
      };
      if (mode === 'workflow') await new Scheduler(this, runId, task, sourceFile).run();
      else {
        this.store.command('transition', {
          run_id: runId,
          status: 'running',
          stage: stages[mode] ?? 'intake',
        });
        if (mode === 'gameplay') await play(this, runId, manifest, task);
        else {
          const remaining = this.deadline.remaining();
          if (remaining !== null) timeout = Math.min(timeout || Infinity, remaining / 1000);
          this.check(runId);
          const stageInput = { mode, task, manifest, source_file: sourceFile, timeout_seconds: timeout };
          await this.tool('stage', runId, stageInput);
        }
      }
      this.check(runId);
      this.store.command('transition', {
        run_id: runId,
        status: 'succeeded',
        stage: mode === 'workflow' ? this.store.run(runId)['stage'] : (stages[mode] ?? 'intake'),
      });
    } catch (error) {
      this.deadline.check();
      const failure = frameworkError(
        this.signal.aborted && this.signal.reason instanceof FrameworkError
          ? this.signal.reason
          : error,
      );
      let status =
        failure.code === ErrorCode.CANCELLED
          ? 'cancelled'
          : blockedErrors.has(failure.code) ||
              [ErrorCode.CLOSED, ErrorCode.PREVIOUS_PROCESS_EXITED].includes(failure.code)
            ? 'blocked'
            : 'failed';
      if (
        ['queued', 'blocked'].includes(text(this.store.run(runId)['status'])) &&
        status === 'failed'
      )
        status = 'blocked';
      if (this.deadline.expired())
        this.store.event(runId, 'task.deadline.exhausted', this.deadline.context());
      this.store.command('transition', { run_id: runId, status, reason: failure.details() });
    } finally {
      try {
        if (loadedManifest && settledStatuses.has(text(this.store.run(runId)['status'])))
          await finalizeGames(this, runId, loadedManifest);
      } finally {
        if (ownsDeadline) this.deadline.dispose();
        this.store.finishSegment(runId);
        if (!this.store.input(runId)['workflow_run'] &&
            settledStatuses.has(text(this.store.run(runId)['status']))) {
          if (mode === 'workflow') {
            for (const child of this.store.workflows.children(runId)) {
              if (this.reportingTimeLeft() <= 0) break;
              if (settledStatuses.has(child.status)) await this.publish(child.id);
            }
          }
          await this.publish(runId);
        }
      }
    }
    return this.store.run(runId);
  }
}
