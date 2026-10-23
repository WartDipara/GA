import { existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Store } from '../storage/store.js';
import {
  atomicJson,
  atomicText,
  integer,
  optionalText,
  uid,
  within,
  type Fields,
} from '../storage/values.js';
import { requireDiskSpace } from '../storage/disk.js';
import { Scheduler } from './scheduler.js';
import { blockedErrors, settledStatuses } from './state-policy.js';

export type Tool = (operation: string, runId: string, input: Fields) => Promise<unknown>;
const boundaries: Record<string, string[]> = {
  'call.started': ['call.completed', 'call.failed', 'call.incomplete'],
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
  constructor(
    readonly store: Store,
    readonly tool: Tool,
    private readonly signal: AbortSignal,
    private readonly drain: () => Promise<void>,
  ) {}

  check(runId: string): void {
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
      await this.publish(runId);
      if (failure.code === ErrorCode.CANCELLED) throw failure;
    }
    return runId;
  }

  async recover(): Promise<void> {
    await this.drain();
    const runs = this.store.database.all(
      "SELECT id FROM runs WHERE status='running' ORDER BY rowid",
    );
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
  }

  async repairReports(): Promise<unknown[]> {
    const repaired: unknown[] = [];
    const rows = this.store.database.all(
      "SELECT DISTINCT run_id FROM events WHERE kind='report.requested' ORDER BY run_id",
    );
    for (const row of rows) {
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
        } catch {
          complete = false;
        }
      }
      if (!complete) repaired.push(await this.publish(runId));
    }
    return repaired;
  }

  async publish(runId: string): Promise<unknown> {
    if (!settledStatuses.has(text(this.store.run(runId)['status'])))
      throw new FrameworkError(ErrorCode.REPORT_NOT_READY);
    const state = this.store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
    const previous = this.store.events(runId, { kinds: ['report.requested'], limit: 1 }).at(-1);
    if (!previous || previous.payload['state_event_id'] !== (state?.event_id ?? null)) {
      const request = {
        request_id: uid('report'),
        state_event_id: state?.event_id ?? null,
        business_status: this.store.run(runId)['status'],
      };
      this.store.event(runId, 'report.requested', request);
    }
    try {
      return await this.tool('report', runId, {});
    } catch (error) {
      // A disconnected client or failed renderer cannot discard the already committed outcome.
      const request =
        this.store.events(runId, { kinds: ['report.requested'], limit: 1 }).at(-1)?.payload ?? {};
      const run = this.store.run(runId);
      const publicationId = uid('publication');
      const snapshot = {
        generated_at: new Date().toISOString(),
        business_status: run['status'],
        stage: run['stage'],
        source_event_seq: this.store.watermark(runId),
        kind: run['status'] === 'blocked' ? 'checkpoint' : 'final',
      };
      const receipt: Fields = {
        ...request,
        publication_id: publicationId,
        snapshot,
        status: 'degraded',
        error: frameworkError(error).details(),
        components: {},
        files: [],
      };
      try {
        const directory = join(this.store.runDirectory(runId), 'reports');
        const outcome = {
          run,
          snapshot,
          report_status: 'degraded',
          reason:
            this.store.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1)?.payload[
              'reason'
            ] ?? null,
        };
        atomicJson(join(directory, 'execution.json'), outcome);
        const contents = JSON.stringify(outcome, null, 2)
          .replaceAll('&', '&amp;')
          .replaceAll('<', '&lt;')
          .replaceAll('>', '&gt;');
        const document = `<!doctype html><meta charset="utf-8"><title>Execution outcome</title><h1>Execution outcome</h1><p>Rich report rendering is unavailable. Recorded outcomes remain authoritative.</p><pre>${contents}</pre>`;
        atomicText(join(directory, 'execution.html'), document);
        atomicText(join(directory, 'trace.html'), document);
        atomicJson(join(directory, 'publication.json'), receipt);
      } catch (failure) {
        receipt['fallback_error'] = frameworkError(failure).details();
      }
      this.store.event(runId, 'report.finished', receipt);
      return receipt;
    }
  }

  async run(runId: string, sourceFile: string | null): Promise<Fields> {
    this.store.checkCleanup();
    const run = this.store.run(runId);
    const mode = text(run['mode']);
    if (['succeeded', 'failed', 'cancelled'].includes(text(run['status'])))
      throw new FrameworkError(ErrorCode.RUN_TERMINAL);
    try {
      if (
        ![
          'game',
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
      this.check(runId);
      const manifest = await this.manifest(runId);
      const unknown = this.store.database.one(
        "SELECT 1 FROM actions WHERE run_id=? AND status='unknown'",
        runId,
      );
      if (!['device', 'gameplay'].includes(mode) && unknown)
        throw new FrameworkError(ErrorCode.ACTION_ALREADY_DISPATCHED);
      this.store.command('transition', { run_id: runId, status: 'running' });
      const application = object(manifest['application']);
      const storage = object(application['storage']);
      const reserve = integer(storage['reserve_bytes']);
      const overhead = integer(storage['overhead_bytes_per_apk']);
      const margin = integer(storage['safety_margin_percent']);
      if (reserve < 1 || overhead < 1 || margin < 1 || margin > 100)
        throw new FrameworkError(ErrorCode.INVALID_STORAGE_POLICY);
      requireDiskSpace(this.store.runDirectory(runId), reserve);
      let task = this.store.input(runId);
      let timeout = 0;
      if (mode === 'preprocessing')
        timeout = integer(object(application['preprocessing'])['batch_timeout_seconds']);
      else if (
        !['workflow', 'analysis', 'emulators', 'device', 'observation', 'gameplay'].includes(mode)
      )
        timeout =
          task['kind'] === 'document' || mode === 'intake'
            ? integer(object(application['intake'] ?? {})['timeout_seconds'] ?? 300)
            : integer(object(task['limits'])['timeout_seconds']);
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
        const result = await this.tool('stage', runId, {
          mode,
          task,
          manifest,
          source_file: sourceFile,
          timeout_seconds: timeout,
        });
        if (mode === 'game' || mode === 'intake') task = object(result);
        if (mode === 'game') {
          this.store.command('transition', {
            run_id: runId,
            status: 'running',
            stage: 'environment',
          });
          await this.tool('stage', runId, {
            mode: 'environment',
            task,
            manifest,
            source_file: sourceFile,
            timeout_seconds: timeout,
          });
          this.store.command('transition', { run_id: runId, status: 'running', stage: 'launch' });
          throw new FrameworkError(ErrorCode.DEVICE_NOT_IMPLEMENTED);
        }
      }
      this.check(runId);
      this.store.command('transition', {
        run_id: runId,
        status: 'succeeded',
        stage: mode === 'workflow' ? this.store.run(runId)['stage'] : (stages[mode] ?? 'intake'),
      });
    } catch (error) {
      if (this.signal.aborted) await this.drain();
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
      this.store.command('transition', { run_id: runId, status, reason: failure.details() });
    } finally {
      if (settledStatuses.has(text(this.store.run(runId)['status']))) await this.publish(runId);
    }
    return this.store.run(runId);
  }
}
