import { existsSync, mkdirSync } from 'node:fs';
import { and, eq, or } from 'drizzle-orm';
import { join, relative } from 'node:path';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { ActionRepository } from './action-repository.js';
import { Database } from './database.js';
import { EventRepository, type EventQuery } from './event-repository.js';
import { TaskRepository } from './task-repository.js';
import { WorkflowRepository } from './workflow-repository.js';
import { insertionOrder } from './sqlite-expressions.js';
import {
  deviceBindings,
  eventUsage,
  explorationRecords,
  gameplayProgress,
  runInputs,
  runs,
  sessions,
  taskAliases,
  tasks,
} from './tables.js';
import { performance } from 'node:perf_hooks';
import { elementVerification } from '../exploration/verification.js';
import { atomicJson, within } from '../utilities/files.js';
import { canonical, integer, optionalText, parsed, strings, uid, type Fields } from '../utilities/values.js';
import { redact } from '../utilities/redaction.js';

export interface TraceIds {
  span_id?: string | null;
  parent_span_id?: string | null;
  request_id?: string | null;
  event_id?: string | null;
}

type StorageMethod = ContractMap['kernel-command']['method'];
export type Event = ContractMap['trace'] & { seq: number };
function subject(task: Fields): string {
  if (task['kind'] === 'apk_batch' || task['kind'] === 'apk_workflow')
    return `${strings(task['source_ids']).length}-apks`;
  if (task['kind'] === 'document')
    return text(task['filename'])
      .replace(/^.*[\\/]/, '')
      .replace(/\.[^.]*$/, '');
  return typeof task['target'] === 'string' ? task['target'] : 'task';
}

export class Store {
  private readonly databaseOwner: Database;
  private readonly actionRepository: ActionRepository;
  private readonly eventRepository: EventRepository;
  readonly workflows: WorkflowRepository;
  private readonly taskRecords: TaskRepository;

  private get orm() {
    return this.databaseOwner.orm;
  }

  get database(): Omit<Database, 'orm'> {
    return this.databaseOwner;
  }

  get actionRecords(): Omit<ActionRepository, 'insert' | 'update'> {
    return this.actionRepository;
  }

  get eventRecords(): Omit<EventRepository, 'append'> {
    return this.eventRepository;
  }
  private readonly segments = new Map<
    string,
    { span: string; started: number; payload: ContractMap['execution-segment'] }
  >();

  constructor(readonly root: string) {
    this.databaseOwner = new Database(join(root, 'var'));
    this.actionRepository = new ActionRepository(this.orm);
    this.eventRepository = new EventRepository(this.orm);
    this.workflows = new WorkflowRepository(this.orm);
    this.taskRecords = new TaskRepository(this.orm);
  }

  segmentSpan(runId: string): string | null {
    return this.segments.get(runId)?.span ?? null;
  }

  beginSegment(runId: string): void {
    if (this.segments.has(runId)) throw new FrameworkError(ErrorCode.RUN_BUSY);
    const previous = this.events(runId, { kinds: ['execution.segment.started'], limit: 1 }).at(-1);
    if (previous?.span_id) {
      const endings = this.events(runId, {
        kinds: ['execution.segment.finished', 'execution.segment.incomplete'],
        span: previous.span_id,
        limit: 1,
      });
      if (!endings.length) {
        const interrupted = decode('execution-segment', {
          ...previous.payload,
          status: 'unknown',
          duration_ms: null,
          error: { code: ErrorCode.PREVIOUS_PROCESS_EXITED },
        });
        this.event(runId, 'execution.segment.incomplete', object(interrupted), {
          span_id: previous.span_id,
        });
      }
    }
    const payload = decode('execution-segment', {
      capability: 'workflow.attempt',
      attempt_id: uid('attempt'),
      previous_span_id: previous?.span_id ?? null,
      status: 'running',
      duration_ms: null,
    });
    const span = uid('span');
    this.event(runId, 'execution.segment.started', object(payload), { span_id: span });
    this.segments.set(runId, { span, started: performance.now(), payload });
  }

  finishSegment(runId: string): void {
    const segment = this.segments.get(runId);
    if (!segment) return;
    try {
      const payload = decode('execution-segment', {
        ...segment.payload,
        status: this.run(runId)['status'],
        duration_ms: Math.round(performance.now() - segment.started),
      });
      this.event(runId, 'execution.segment.finished', object(payload), { span_id: segment.span });
    } finally {
      this.segments.delete(runId);
    }
  }

  run(runId: string): Fields {
    const run = this.orm.select().from(runs).where(eq(runs.id, runId)).get();
    if (!run) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Execution not found');
    return run;
  }

  input(runId: string): Fields {
    const row = this.orm
      .select({ spec: runInputs.spec })
      .from(runInputs)
      .where(eq(runInputs.run_id, runId))
      .get();
    if (!row) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Execution input not found');
    return parsed(row['spec']);
  }

  events(runId: string, query: EventQuery = {}): Event[] {
    this.run(runId);
    const records = this.eventRecords.range(runId, query).map((row) => {
      const event = { ...decode('trace', parsed(row.data)), seq: row.seq };
      return event;
    });
    return records;
  }

  taskRuns(taskId: string): Fields[] {
    return this.orm
      .select()
      .from(runs)
      .where(eq(runs.task_id, taskId))
      .orderBy(insertionOrder(runs))
      .all();
  }

  watermark(runId: string): number {
    return this.eventRecords.watermark(runId);
  }

  eventBytes(runId: string): number {
    const row = this.orm
      .select({ size: eventUsage.size_bytes })
      .from(eventUsage)
      .where(eq(eventUsage.run_id, runId))
      .get();
    return row?.size ?? 0;
  }

  reservedApk(instanceId: string): string | undefined {
    return this.orm
      .select({ key: deviceBindings.apk_key })
      .from(deviceBindings)
      .where(eq(deviceBindings.instance_id, instanceId))
      .get()?.key;
  }

  session(runId: string, role: string): string | null {
    const row = this.orm
      .select({ thread: sessions.thread_id })
      .from(sessions)
      .where(and(eq(sessions.run_id, runId), eq(sessions.role, role)))
      .get();
    return row?.thread ?? null;
  }

  progress(key: string): Fields | null {
    const row = this.orm
      .select()
      .from(gameplayProgress)
      .where(eq(gameplayProgress.workspace_key, key))
      .get();
    if (!row) return null;
    const progress = { run_id: row.run_id, data: parsed(row.data) };
    return progress;
  }

  exploration(key: string): ContractMap['exploration-record'][] {
    const rows = this.orm
      .select({ data: explorationRecords.data })
      .from(explorationRecords)
      .where(eq(explorationRecords.workspace_key, key))
      .orderBy(insertionOrder(explorationRecords))
      .all();
    return rows.map((row) => decode('exploration-record', parsed(row.data)));
  }

  private commitExploration(runId: string, params: Fields, ids: TraceIds): void {
    const key = text(params['workspace_key']);
    const taskId = text(this.run(runId)['task_id']);
    if (!key.startsWith(`${taskId}:`)) throw new FrameworkError(ErrorCode.INVALID_PATH);
    const values = params['records'];
    if (!Array.isArray(values)) throw new FrameworkError(ErrorCode.INVALID_CONTRACT);
    for (const value of values) {
      const record = decode('exploration-record', value);
      if (
        record.kind === 'element' &&
        (record.status === 'verified' || record.data.ever_verified)
      ) {
        const verification = elementVerification(this, key, record);
        if (verification.status !== 'verified')
          throw new FrameworkError(
            ErrorCode.INVALID_STATE,
            `Exploration verification rejected: ${verification.reason}`,
          );
        record.data.verification = verification.proof;
      }
      if (record.kind === 'edge' && record.status === 'verified') {
        const row = this.orm
          .select({ data: explorationRecords.data })
          .from(explorationRecords)
          .where(
            and(
              eq(explorationRecords.workspace_key, key),
              eq(explorationRecords.kind, 'element'),
              eq(explorationRecords.id, record.data.element_id),
            ),
          )
          .get();
        const element = row ? decode('exploration-record', parsed(row['data'])) : null;
        const verification =
          element?.kind === 'element'
            ? elementVerification(this, key, element, record.data.action_id)
            : null;
        if (
          verification?.status !== 'verified' ||
          verification.proof.after_observation_id !== record.data.observation_id
        )
          throw new FrameworkError(ErrorCode.INVALID_STATE, 'Exploration edge proof is incomplete');
      }
      const row = {
        workspace_key: key,
        kind: record.kind,
        id: record.id,
        task_id: taskId,
        run_id: runId,
        data: JSON.stringify(record),
      };
      this.orm
        .insert(explorationRecords)
        .values(row)
        .onConflictDoUpdate({
          target: [
            explorationRecords.workspace_key,
            explorationRecords.kind,
            explorationRecords.id,
          ],
          set: { run_id: row.run_id, data: row.data },
        })
        .run();
      this.event(
        runId,
        `exploration.${record.kind}.updated`,
        {
          workspace_key: key,
          record,
        },
        ids,
      );
    }
  }

  taskSupplements(taskId: string): Fields[] {
    return this.eventRecords.taskEvents(taskId, 'task.supplement').map((row) => parsed(row.data));
  }

  runDirectory(runId: string): string {
    const taskDirectory = within(
      join(this.root, 'var'),
      `tasks/${text(this.run(runId)['task_id'])}`,
    );
    mkdirSync(taskDirectory, { recursive: true });
    const current = within(taskDirectory, `executions/${runId}`);
    const legacy = within(join(this.root, 'var'), `runs/${runId}`);
    if (existsSync(current) && existsSync(legacy))
      throw new FrameworkError(ErrorCode.WORKSPACE_CONFLICT);
    return existsSync(legacy) ? legacy : current;
  }

  artifact(runId: string, reference: string, value: unknown): string {
    const started = performance.now();
    let failed = true;
    try {
      const directory = this.runDirectory(runId);
      mkdirSync(directory, { recursive: true });
      const path = within(directory, reference);
      atomicJson(path, redact(value));
      failed = false;
      return relative(directory, path).replaceAll('\\', '/');
    } catch (error) {
      this.database.failure ??= new FrameworkError(ErrorCode.TRACE_UNAVAILABLE, undefined, false, {
        cause: error,
      });
      throw this.database.failure;
    } finally {
      this.database.health.record('artifact_write', started, failed);
    }
  }

  health(runId: string, force = false): void {
    if (this.database.failure) return;
    const snapshot = this.database.health.snapshot(this.database.directory, force);
    if (snapshot) this.event(runId, 'trace.writer.health', object(snapshot));
  }

  event(runId: string, kind: string, payload: Fields, ids: TraceIds = {}): Fields {
    return this.database.transaction(() => {
      const taskId = this.run(runId)['task_id'];
      const started = performance.now();
      let failed = true;
      let encoded: string | undefined;
      let event: Fields;
      try {
        event = {
          event_id: ids.event_id || uid('evt'),
          task_id: taskId,
          run_id: runId,
          span_id: ids.span_id ?? null,
          parent_span_id:
            ids.parent_span_id ??
            (ids.span_id && ids.span_id !== this.segmentSpan(runId)
              ? this.segmentSpan(runId)
              : null),
          request_id: ids.request_id ?? null,
          kind,
          timestamp: new Date().toISOString(),
          payload: redact(payload),
        };
        encoded = JSON.stringify(event);
        failed = false;
      } finally {
        this.database.health.record(
          'event_encode',
          started,
          failed,
          encoded === undefined ? null : Buffer.byteLength(encoded),
        );
      }
      const previous = this.eventRecords.byId(text(event['event_id']));
      if (previous) {
        const old = parsed(previous['data']);
        const comparable = { ...event, timestamp: old['timestamp'] };
        if (canonical(old) !== canonical(comparable))
          throw new FrameworkError(ErrorCode.EVENT_ID_CONFLICT);
        return old;
      }
      const row = {
        event_id: text(event['event_id']),
        run_id: runId,
        span_id: ids.span_id ?? null,
        kind,
        data: encoded,
      };
      this.eventRepository.append(row);
      return event;
    });
  }

  eventBatch(runId: string, value: unknown, ids: TraceIds): void {
    const entries = decode('plugin-event-batch', value);
    this.database.transaction(() => {
      for (const entry of entries) {
        this.event(runId, entry.kind, object(entry.payload), { ...ids, event_id: entry.event_id });
      }
    });
  }

  latestAction(runId: string): Fields | null {
    const task = this.input(runId);
    const apkKey = `${String(task['source_run'])}:${String(task['apk_index'])}`;
    const row = this.actionRecords.latest(apkKey, task['kind'] === 'gameplay');
    if (!row) return null;
    const action = {
      run_id: row['run_id'],
      action_id: row['action_id'],
      status: row['status'],
      ...parsed(row['result']),
    };
    return action;
  }

  checkCleanup(): void {
    if (existsSync(join(this.root, 'var/cleanup/pending.json')))
      throw new FrameworkError(ErrorCode.CLEANUP_PENDING);
  }

  private canonicalTask(taskId: string): string {
    const alias = this.orm
      .select({ owner: taskAliases.task_id })
      .from(taskAliases)
      .where(eq(taskAliases.alias_id, taskId))
      .get();
    return alias?.owner ?? taskId;
  }

  private task(taskId: string): Fields {
    const row = this.orm.select({ spec: tasks.spec }).from(tasks).where(eq(tasks.id, taskId)).get();
    if (!row) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Task not found');
    return parsed(row.spec);
  }

  private writeTask(taskId: string): void {
    const directory = within(join(this.root, 'var'), `tasks/${taskId}`);
    atomicJson(join(directory, 'task.json'), { task_id: taskId, submission: this.task(taskId) });
  }

  private submit(params: Fields): string {
    this.checkCleanup();
    const spec = object(redact(params['spec']));
    const key = optionalText(params['key']);
    if (key) {
      const previous = this.orm
        .select({ id: tasks.id, spec: tasks.spec })
        .from(tasks)
        .where(eq(tasks.submission_key, key))
        .get();
      if (previous) {
        if (canonical(parsed(previous['spec'])) !== canonical(spec))
          throw new FrameworkError(ErrorCode.IDEMPOTENCY_CONFLICT);
        const taskId = this.canonicalTask(text(previous['id']));
        this.writeTask(taskId);
        return taskId;
      }
    }
    const label =
      subject(spec)
        .toLowerCase()
        .replaceAll(/[^a-z0-9._-]+/g, '-')
        .slice(0, 40)
        .replace(/^[._-]+|[._-]+$/g, '') || 'task';
    for (let attempt = 0; attempt < 10; attempt++) {
      const taskId = uid(`task_${label}`);
      const directory = within(join(this.root, 'var'), `tasks/${taskId}`);
      mkdirSync(join(this.root, 'var/tasks'), { recursive: true });
      if (
        this.orm.select({ id: tasks.id }).from(tasks).where(eq(tasks.id, taskId)).get() ||
        existsSync(directory)
      )
        continue;
      mkdirSync(directory);
      const row = {
        id: taskId,
        submission_key: key,
        spec: JSON.stringify(spec),
        created_at: new Date().toISOString(),
      };
      this.orm.insert(tasks).values(row).run();
      this.writeTask(taskId);
      return taskId;
    }
    throw new FrameworkError(ErrorCode.TASK_ID_COLLISION);
  }

  private createRun(params: Fields): string {
    this.checkCleanup();
    const taskId = this.canonicalTask(text(params['task_id']));
    const spec = params['spec'] == null ? this.task(taskId) : object(redact(params['spec']));
    this.task(taskId);
    const source = spec['prepared_run'] ?? spec['source_run'];
    if (source && this.run(text(source))['task_id'] !== taskId)
      throw new FrameworkError(ErrorCode.INVALID_STATE, 'Execution source belongs to another task');
    const directory = within(join(this.root, 'var'), `tasks/${taskId}/executions`);
    mkdirSync(directory, { recursive: true });
    for (let attempt = 0; attempt < 10; attempt++) {
      const runId = uid('run');
      const path = within(directory, runId);
      if (
        this.orm.select({ id: runs.id }).from(runs).where(eq(runs.id, runId)).get() ||
        existsSync(path)
      )
        continue;
      mkdirSync(path);
      const timestamp = new Date().toISOString();
      const mode = text(params['mode']);
      const row = {
        id: runId,
        task_id: taskId,
        mode,
        status: 'queued',
        stage: 'intake',
        created_at: timestamp,
        updated_at: timestamp,
      };
      this.orm.insert(runs).values(row).run();
      this.orm
        .insert(runInputs)
        .values({ run_id: runId, spec: JSON.stringify(spec) })
        .run();
      this.event(runId, 'run.created', { mode, name: subject(spec) });
      this.writeTask(taskId);
      return runId;
    }
    throw new FrameworkError(ErrorCode.RUN_ID_COLLISION);
  }

  private transition(runId: string, params: Fields): void {
    const run = this.run(runId);
    const status = text(params['status']);
    const stage = optionalText(params['stage']) || text(run['stage']);
    const allowed: Record<string, string[]> = {
      queued: ['running', 'cancelled', 'blocked'],
      running: ['running', 'blocked', 'failed', 'succeeded', 'cancelled'],
      blocked: ['running', 'cancelled', 'blocked'],
      failed: [],
      succeeded: [],
      cancelled: [],
    };
    if (!allowed[text(run['status'])]?.includes(status)) {
      if (status === run['status']) return;
      throw new FrameworkError(
        ErrorCode.INVALID_STATE,
        `Cannot change ${text(run['status'])} to ${status}`,
      );
    }
    const state = this.event(runId, 'run.state', {
      from: run['status'],
      to: status,
      stage,
      reason: params['reason'] ?? null,
    });
    const update = { status, stage, updated_at: new Date().toISOString() };
    this.orm.update(runs).set(update).where(eq(runs.id, runId)).run();
    if (['blocked', 'failed', 'succeeded', 'cancelled'].includes(status)) {
      const request = {
        request_id: uid('report'),
        state_event_id: state['event_id'],
        business_status: status,
      };
      this.event(runId, 'report.requested', request);
    }
  }

  private reserve(runId: string, params: Fields): void {
    const instance = text(params['instance_id']);
    const apkKey = text(params['apk_key']);
    const index = integer(params['instance_index']);
    const rows = this.orm
      .select()
      .from(deviceBindings)
      .where(or(eq(deviceBindings.instance_id, instance), eq(deviceBindings.apk_key, apkKey)))
      .all();
    if (rows.length) {
      const row = rows[0];
      if (
        !row ||
        rows.length !== 1 ||
        row['instance_id'] !== instance ||
        row['apk_key'] !== apkKey ||
        row['instance_index'] !== index
      )
        throw new FrameworkError(ErrorCode.DEVICE_BINDING_CONFLICT);
      if (row['run_id'] !== runId)
        this.event(runId, 'device.reservation.reused', {
          instance_id: instance,
          reservation_run: row['run_id'],
        });
      return;
    }
    const binding = {
      instance_id: instance,
      apk_key: apkKey,
      instance_index: index,
      run_id: runId,
    };
    this.orm.insert(deviceBindings).values(binding).run();
    this.event(runId, 'device.reserved', {
      instance_id: instance,
      instance_index: index,
      apk_key: apkKey,
    });
  }

  private releaseDevice(runId: string, apply: boolean): Fields {
    this.checkCleanup();
    if (this.workflows.hasRunning()) throw new FrameworkError(ErrorCode.RUN_BUSY);
    const binding = this.orm
      .select()
      .from(deviceBindings)
      .where(eq(deviceBindings.run_id, runId))
      .get();
    if (!binding) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Reservation not found');
    const unresolved = this.actionRecords.unresolvedForWorkspace(
      binding.instance_id,
      binding.apk_key,
    );
    const result = {
      reservation: binding,
      released: apply,
      unresolved_actions: unresolved,
      application_data_changed: false,
      historical_outcomes_changed: false,
    };
    if (apply) {
      this.event(runId, 'device.reservation.released', result);
      this.orm.delete(deviceBindings).where(eq(deviceBindings.run_id, runId)).run();
      const state = this.events(runId, { kinds: ['run.state'], limit: 1 }).at(-1);
      this.event(runId, 'report.requested', {
        request_id: uid('report'),
        state_event_id: state?.event_id ?? null,
        business_status: this.run(runId)['status'],
      });
    }
    return result;
  }

  private beginAction(runId: string, params: Fields): string {
    const run = this.run(runId);
    if (run['status'] !== 'running') throw new FrameworkError(ErrorCode.INVALID_STATE);
    if (run['cancel_requested']) throw new FrameworkError(ErrorCode.CANCELLED);
    const instance = text(params['instance_id']);
    const capability = text(params['capability']);
    const reservedApk = this.reservedApk(instance);
    const task = this.input(runId);
    const apkKey = `${String(task['source_run'])}:${String(task['apk_index'])}`;
    if (reservedApk !== apkKey || !['device_preparation', 'gameplay'].includes(text(task['kind'])))
      throw new FrameworkError(ErrorCode.DEVICE_BINDING_CONFLICT);
    const count = this.actionRecords.total(runId);
    if (params['max_actions'] != null && count >= integer(params['max_actions']))
      throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED);
    const pending = this.actionRecords.hasUnresolvedForWorkspace(instance, apkKey);
    if (pending)
      throw new FrameworkError(
        capability === 'device.install'
          ? ErrorCode.DEVICE_INSTALL_UNKNOWN
          : ErrorCode.ACTION_ALREADY_DISPATCHED,
      );
    const actionId = uid('action');
    const spanId = text(params['span_id']);
    const details = {
      capability,
      input_ref: text(params['input_ref']),
      instance_id: instance,
      span_id: spanId,
    };
    const action = {
      run_id: runId,
      action_id: actionId,
      status: 'prepared',
      result: JSON.stringify(details),
    };
    this.actionRepository.insert(action);
    this.event(runId, 'action.intent', { action_id: actionId, ...details }, { span_id: spanId });
    return actionId;
  }

  private action(runId: string, params: Fields, dispatch: boolean): void {
    const actionId = text(params['action_id']);
    const row = this.actionRecords.byId(runId, actionId);
    if (!row) throw new FrameworkError(ErrorCode.INVALID_STATE);
    const previous = text(row['status']);
    const details = parsed(row['result']);
    if (dispatch) {
      const run = this.run(runId);
      if (run['status'] !== 'running') throw new FrameworkError(ErrorCode.INVALID_STATE);
      if (run['cancel_requested']) throw new FrameworkError(ErrorCode.CANCELLED);
      if (previous !== 'prepared') throw new FrameworkError(ErrorCode.INVALID_STATE);
      this.actionRepository.update(runId, actionId, 'unknown');
      this.event(
        runId,
        'action.dispatched',
        { action_id: actionId, ...details },
        { span_id: text(details['span_id']) },
      );
      return;
    }
    const status = text(params['status']);
    if (
      !['executed', 'rejected', 'verified', 'reconciled'].includes(status) ||
      !['prepared', 'unknown', 'executed'].includes(previous) ||
      (previous === 'prepared' && status !== 'rejected')
    )
      throw new FrameworkError(ErrorCode.INVALID_STATE);
    const result = { ...details, result: redact(params['result']) };
    this.actionRepository.update(runId, actionId, status, JSON.stringify(result));
    this.event(
      runId,
      'action.result',
      { action_id: actionId, from: previous, status, ...result },
      { span_id: text(details['span_id']) },
    );
  }

  command(method: StorageMethod, params: Fields): unknown {
    const request = { method, input: params };
    return this.accept(request);
  }

  accept(value: unknown): unknown {
    const command = decode('kernel-command', value);
    const method = command.method;
    const params = object(command.input);
    if (!['delete_tasks', 'compact'].includes(method)) this.checkCleanup();
    if (method === 'compact') {
      this.database.compact();
      return null;
    }
    return this.database.transaction(() => {
      if (method === 'submit') return this.submit(params);
      if (method === 'create_run') return this.createRun(params);
      if (method === 'delete_tasks') {
        this.taskRecords.deleteOwned(strings(params['task_ids']));
        return null;
      }
      const runId = text(params['run_id']);
      const ids: TraceIds = {
        span_id: optionalText(params['span_id']),
        parent_span_id: optionalText(params['parent_span_id']),
        request_id: optionalText(params['request_id']),
        event_id: optionalText(params['event_id']),
      };
      switch (method) {
        case 'event':
          return this.event(runId, text(params['kind']), object(params['payload']), ids);
        case 'event_batch':
          this.eventBatch(runId, params['entries'], ids);
          break;
        case 'transition':
          this.transition(runId, params);
          break;
        case 'save_session': {
          const role = text(params['role']);
          const threadId = text(params['thread_id']);
          const session = { run_id: runId, role, thread_id: threadId };
          this.orm
            .insert(sessions)
            .values(session)
            .onConflictDoUpdate({
              target: [sessions.run_id, sessions.role],
              set: { thread_id: threadId },
            })
            .run();
          this.event(runId, 'ai.session', { role, thread_id: threadId });
          break;
        }
        case 'cancel':
          if (!['succeeded', 'failed', 'cancelled'].includes(text(this.run(runId)['status']))) {
            this.orm.update(runs).set({ cancel_requested: 1 }).where(eq(runs.id, runId)).run();
            this.event(runId, 'run.cancel_requested', {});
          }
          break;
        case 'supplement': {
          const value = text(params['text']);
          if (!value.trim() || value.includes('\0') || Buffer.byteLength(value) > 200_000)
            throw new FrameworkError(ErrorCode.INVALID_DOCUMENT);
          const run = this.run(runId);
          if (run['mode'] === 'preprocessing')
            throw new FrameworkError(ErrorCode.APK_INPUT_REQUIRED);
          if (run['status'] !== 'blocked') throw new FrameworkError(ErrorCode.INVALID_STATE);
          if (
            run['mode'] === 'intake' &&
            this.eventRecords.hasKind(runId, 'intake.accepted')
          )
            throw new FrameworkError(ErrorCode.INTAKE_NEW_RUN_REQUIRED);
          return this.event(runId, 'task.supplement', { text: value });
        }
        case 'reserve_device':
          this.reserve(runId, params);
          break;
        case 'release_device':
          return this.releaseDevice(runId, params['apply'] === true);
        case 'begin_device_action':
          return this.beginAction(runId, params);
        case 'dispatch_device_action':
          this.action(runId, params, true);
          break;
        case 'finish_device_action':
          this.action(runId, params, false);
          break;
        case 'checkpoint': {
          const key = text(params['workspace_key']);
          const data = redact(params['data']);
          const checkpoint = { workspace_key: key, run_id: runId, data: JSON.stringify(data) };
          this.orm
            .insert(gameplayProgress)
            .values(checkpoint)
            .onConflictDoUpdate({
              target: gameplayProgress.workspace_key,
              set: { run_id: checkpoint.run_id, data: checkpoint.data },
            })
            .run();
          this.event(runId, 'gameplay.checkpoint', { workspace_key: key, progress: data });
          break;
        }
        case 'exploration_commit':
          this.commitExploration(runId, params, ids);
          break;
        default: {
          const unsupported: never = method;
          throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND, unsupported);
        }
      }
      return null;
    });
  }
}
