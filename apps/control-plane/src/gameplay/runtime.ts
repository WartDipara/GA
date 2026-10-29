import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { FileUsage } from '../storage/file-usage.js';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Host } from '../kernel/host.js';
import { requireDiskSpace } from '../storage/disk.js';
import { integer, uid, within, type Fields } from '../storage/values.js';
import type { Execution } from '../workflow/execution.js';
import { ProgressStore } from './progress.js';
import type { Action, Facts, Observation, Pending } from './types.js';
import { Diagnostics } from './diagnostics.js';

export class Runtime {
  readonly store;
  readonly directory: string;
  readonly host: Host;
  readonly diagnostics: Diagnostics;
  readonly usage: FileUsage;
  readonly cache = new Map<string, { facts: Facts; event: string }>();
  step: string | null = null;
  private started = 0;
  watchdog: { check(): void; observeProgress(proof: string): void } | null = null;

  constructor(
    readonly execution: Execution,
    readonly runId: string,
    readonly task: Fields,
    readonly manifest: Fields,
    readonly session: Fields,
    readonly credentialPath: string,
    readonly progress: ProgressStore,
    host: Host,
  ) {
    this.store = execution.store;
    this.directory = this.store.runDirectory(runId);
    this.host = host;
    this.usage = new FileUsage(this.directory);
    this.diagnostics = new Diagnostics(this);
  }

  check(): void {
    this.execution.check(this.runId);
    if (this.host.failure) throw this.host.failure;
    this.watchdog?.check();
  }

  record(kind: string, payload: Fields, span: string | null = this.step): string {
    const event = this.store.event(this.runId, kind, payload, { span_id: span });
    return text(event['event_id']);
  }

  begin(reason: string): void {
    this.finish();
    this.step = uid('span');
    this.started = performance.now();
    this.record('gameplay.step.started', { capability: 'gameplay.step', reason });
  }

  finish(error?: unknown): void {
    if (!this.step) return;
    const failure = error === undefined ? null : frameworkError(error);
    const status = failure
      ? [ErrorCode.CANCELLED, ErrorCode.PREVIOUS_PROCESS_EXITED].includes(failure.code)
        ? 'incomplete'
        : 'failed'
      : 'completed';
    const payload: Fields = { duration_ms: Math.round(performance.now() - this.started) };
    if (failure) payload['error'] = failure.details();
    const span = this.step;
    this.step = null;
    if (!this.host.failure && !this.store.database.failure)
      this.record(`gameplay.step.${status}`, payload, span);
  }

  async call(
    capability: string,
    input: Fields,
    timeout: number | null = null,
    parent: string | null = this.step,
  ): Promise<unknown> {
    this.check();
    const context = { capability, parent_span_id: parent, timeout };
    try {
      return await this.host.execute('call', input, context);
    } finally {
      if (typeof input['workspace'] === 'string') await this.usage.track(input['workspace']);
      if (typeof input['destination'] === 'string') await this.usage.track(input['destination']);
    }
  }

  async pause(milliseconds: number): Promise<void> {
    this.check();
    await delay(milliseconds, undefined, { signal: this.execution.signal });
    this.check();
  }

  async admit(initial = false): Promise<void> {
    this.check();
    for (const path of this.host.artifacts) {
      await this.usage.track(path);
      this.host.artifacts.delete(path);
    }
    const settings = object(object(this.manifest['application'])['gameplay']);
    const eventBytes = integer(
      this.store.database.one('SELECT size_bytes FROM event_usage WHERE run_id=?', this.runId)?.[
        'size_bytes'
      ] ?? 0,
    );
    const remaining =
      integer(settings['evidence_budget_bytes']) - (await this.usage.total()) - 2 * eventBytes;
    const headroom = 128 * 1024 ** 2;
    if (remaining < headroom) throw new FrameworkError(ErrorCode.EVIDENCE_BUDGET);
    requireDiskSpace(
      this.directory,
      integer(settings['reserve_bytes']) + (initial ? remaining : headroom),
    );
  }

  async observe(): Promise<Observation> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        await this.admit();
        const reference = `artifacts/${uid('observation')}`;
        const request = {
          session: this.session,
          package_name: this.task['target'],
          workspace: within(this.directory, reference),
          sensitive_regions: this.progress.data.sensitive_regions,
          credential_path: this.credentialPath,
        };
        const result = decode(
          'device-capture-result',
          await this.call('device.capture', request, 0),
        );
        confirmSession(this.session, result.session);
        if (result.screenshot.file_name !== 'screen.png')
          throw new FrameworkError(ErrorCode.INVALID_SCREENSHOT);
        const image = await readFile(within(this.directory, `${reference}/screen.png`));
        if (
          image.length < 24 ||
          image.length !== result.screenshot.size_bytes ||
          image.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
          image.readUInt32BE(16) !== result.screenshot.width ||
          image.readUInt32BE(20) !== result.screenshot.height ||
          createHash('sha256').update(image).digest('hex') !== result.screenshot.sha256
        )
          throw new FrameworkError(ErrorCode.INVALID_SCREENSHOT);
        const observation = {
          ...result,
          observation_id: uid('obs'),
          evidence_ref: `${reference}/screen.png`,
        };
        this.record('gameplay.observation', observation);
        return observation;
      } catch (error) {
        if (
          !(error instanceof FrameworkError) ||
          error.code !== ErrorCode.DEVICE_OBSERVATION_UNSTABLE ||
          attempt === 2
        )
          throw error;
        this.record('gameplay.observation.retry', { attempt: attempt + 1 });
        await this.pause(1000);
      }
    }
  }

  async perform(
    action: Action,
    observation: Observation,
    source = 'operator',
    targetGuard: string | null = null,
    pageGuard: ContractMap['perception-locate-result']['page_guard'] = null,
    exploration: Pending['exploration'] | null = null,
  ): Promise<string> {
    await this.admit();
    decode('gameplay-action', action);
    const data = this.progress.data;
    if (data.pending)
      throw new FrameworkError(
        ErrorCode.ACTION_ALREADY_DISPATCHED,
        'Reconcile the pending action first',
      );
    const previousAction = this.store.latestAction(this.runId)?.['action_id'];
    if (action.type === 'credential') {
      if (!action.credential_field || !action.bounds)
        throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
      data.sensitive_regions.push(action.bounds);
    }
    if (action.type === 'launch') {
      if (observation.runtime.alive || data.recovery_rounds >= 2)
        throw new FrameworkError(ErrorCode.GAMEPLAY_BLOCKED);
      data.recovery_rounds += 1;
    }
    data.pending = {
      observation_id: observation.observation_id,
      action,
      run_id: this.runId,
      image_ref: observation.evidence_ref,
      step_id: this.step,
      ...(exploration ? { exploration } : {}),
    };
    this.progress.save();
    const request = {
      session: this.session,
      package_name: this.task['target'],
      runtime: observation.runtime,
      action,
      sensitive_regions: observation.sensitive_regions,
      observation_path: within(this.directory, observation.evidence_ref),
      credential_path: this.credentialPath,
      target_guard: targetGuard,
      page_guard: pageGuard,
    };
    let result: Fields;
    try {
      result = object(await this.host.execute('perform', request, { parent_span_id: this.step }));
    } catch (error) {
      if (this.host.failure || this.store.database.failure) throw error;
      const latest = this.store.latestAction(this.runId);
      const created = latest && latest['action_id'] !== previousAction;
      if (!created || latest['status'] === 'rejected') {
        data.pending = null;
        this.record('gameplay.action.not_dispatched', {
          action_id: created ? latest['action_id'] : null,
          observation_id: observation.observation_id,
          error: frameworkError(error).details(),
        });
      } else {
        data.pending.action_id = text(latest['action_id']);
      }
      this.progress.save();
      throw error;
    }
    const actionId = text(result['action_id']);
    this.record('gameplay.action.origin', { action_id: actionId, source });
    data.pending.action_id = actionId;
    this.progress.save();
    this.diagnostics.request(actionId);
    return actionId;
  }

  outcome(runId: string, actionId: string, status: string, result: Fields): void {
    this.store.command('finish_device_action', {
      run_id: runId,
      action_id: actionId,
      status,
      result,
    });
  }

  checkpoint(observation: Observation): void {
    this.progress.data.checkpoint = {
      run_id: this.runId,
      observation_id: observation.observation_id,
      runtime: observation.runtime,
    };
    this.progress.save();
  }
}

export function confirmSession(expected: Fields, actual: Fields): void {
  if (['instance_id', 'instance_index', 'serial'].some((key) => expected[key] !== actual[key]))
    throw new FrameworkError(ErrorCode.DEVICE_IDENTITY_CHANGED);
}
