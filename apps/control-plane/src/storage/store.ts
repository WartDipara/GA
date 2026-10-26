import { existsSync, mkdirSync } from 'node:fs';
import type { SQLInputValue } from 'node:sqlite';
import { join, relative } from 'node:path';
import { decode, object, text, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { Database } from './database.js';
import { performance } from 'node:perf_hooks';
import { elementVerification } from '../exploration/verification.js';
import {
  atomicJson,
  canonical,
  integer,
  optionalText,
  parsed,
  redact,
  strings,
  uid,
  within,
  type Fields,
} from './values.js';

export interface TraceIds {
  span_id?: string | null;
  parent_span_id?: string | null;
  request_id?: string | null;
  event_id?: string | null;
}

type StorageMethod = ContractMap['kernel-command']['method'];
export type Event = ContractMap['trace'] & { seq: number };
export interface EventQuery {
  after?: number;
  span?: string;
  kinds?: string[];
  limit?: number;
  through?: number;
}

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
  readonly database: Database;

  constructor(readonly root: string) {
    this.database = new Database(join(root, 'var'));
  }

  run(runId: string): Fields {
    const run = this.database.one('SELECT * FROM runs WHERE id=?', runId);
    if (!run) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Execution not found');
    return run;
  }

  input(runId: string): Fields {
    const row = this.database.one('SELECT spec FROM run_inputs WHERE run_id=?', runId);
    if (!row) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Execution input not found');
    return parsed(row['spec']);
  }

  events(runId: string, query: EventQuery = {}): Event[] {
    this.run(runId);
    let sql = 'SELECT seq,data FROM events WHERE run_id=? AND seq>?';
    const arguments_: SQLInputValue[] = [runId, query.after ?? 0];
    if (query.through !== undefined) {
      sql += ' AND seq<=?';
      arguments_.push(query.through);
    }
    if (query.span) {
      sql += ' AND span_id=?';
      arguments_.push(query.span);
    }
    if (query.kinds?.length) {
      sql += ` AND kind IN (${query.kinds.map(() => '?').join(',')})`;
      arguments_.push(...query.kinds);
    }
    sql += query.limit ? ' ORDER BY seq DESC LIMIT ?' : ' ORDER BY seq';
    if (query.limit) arguments_.push(query.limit);
    const events = this.database.all(sql, ...arguments_).map((row) => {
      const event = { ...decode('trace', parsed(row['data'])), seq: integer(row['seq']) };
      return event;
    });
    if (query.limit) events.reverse();
    return events;
  }

  taskRuns(taskId: string): Fields[] {
    const rows = this.database.all('SELECT * FROM runs WHERE task_id=? ORDER BY rowid', taskId);
    return rows;
  }

  watermark(runId: string): number {
    return integer(
      this.database.one('SELECT COALESCE(MAX(seq),0) AS seq FROM events WHERE run_id=?', runId)?.[
        'seq'
      ],
    );
  }

  session(runId: string, role: string): string | null {
    return optionalText(
      this.database.one('SELECT thread_id FROM sessions WHERE run_id=? AND role=?', runId, role)?.[
        'thread_id'
      ],
    );
  }

  progress(key: string): Fields | null {
    const row = this.database.one(
      'SELECT run_id,data FROM gameplay_progress WHERE workspace_key=?',
      key,
    );
    if (!row) return null;
    const progress = { run_id: text(row['run_id']), data: parsed(row['data']) };
    return progress;
  }

  exploration(key: string): ContractMap['exploration-record'][] {
    const rows = this.database.all(
      'SELECT data FROM exploration_records WHERE workspace_key=? ORDER BY rowid',
      key,
    );
    const records = rows.map((row) => decode('exploration-record', parsed(row['data'])));
    return records;
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
        const row = this.database.one(
          "SELECT data FROM exploration_records WHERE workspace_key=? AND kind='element' AND id=?",
          key,
          record.data.element_id,
        );
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
      this.database.execute(
        'INSERT INTO exploration_records VALUES(?,?,?,?,?,?) ON CONFLICT(workspace_key,kind,id) DO UPDATE SET run_id=excluded.run_id,data=excluded.data',
        key,
        record.kind,
        record.id,
        taskId,
        runId,
        JSON.stringify(record),
      );
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
    const rows = this.database.all(
      "SELECT e.data FROM events e JOIN runs r ON r.id=e.run_id WHERE r.task_id=? AND e.kind='task.supplement' ORDER BY e.seq",
      taskId,
    );
    return rows.map((row) => parsed(row['data']));
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
          parent_span_id: ids.parent_span_id ?? null,
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
      const previous = this.database.one(
        'SELECT data FROM events WHERE event_id=?',
        text(event['event_id']),
      );
      if (previous) {
        const old = parsed(previous['data']);
        const comparable = { ...event, timestamp: old['timestamp'] };
        if (canonical(old) !== canonical(comparable))
          throw new FrameworkError(ErrorCode.EVENT_ID_CONFLICT);
        return old;
      }
      this.database.execute(
        'INSERT INTO events(event_id,run_id,span_id,kind,data) VALUES(?,?,?,?,?)',
        text(event['event_id']),
        runId,
        ids.span_id ?? null,
        kind,
        encoded,
      );
      return event;
    });
  }

  eventBatch(runId: string, value: unknown, ids: TraceIds): void {
    if (!Array.isArray(value) || value.length < 1 || value.length > 16)
      throw new FrameworkError(ErrorCode.INVALID_EVENT);
    this.database.transaction(() => {
      for (const value_ of value) {
        const entry = object(value_);
        const kind = text(entry['kind']);
        const eventId = text(entry['event_id']);
        if (
          Object.keys(entry).sort().join(',') !== 'event_id,kind,payload' ||
          !/^(provider|ai|plugin)\./.test(kind) ||
          kind === 'ai.session' ||
          !eventId
        ) {
          throw new FrameworkError(ErrorCode.INVALID_EVENT);
        }
        this.event(runId, kind, object(entry['payload']), { ...ids, event_id: eventId });
      }
    });
  }

  latestAction(runId: string): Fields | null {
    const task = this.input(runId);
    const apkKey = `${String(task['source_run'])}:${String(task['apk_index'])}`;
    const row = this.database.one(
      "SELECT a.run_id,a.action_id,a.status,a.result FROM actions a JOIN device_bindings b ON json_extract(a.result,'$.instance_id')=b.instance_id JOIN run_inputs i ON i.run_id=a.run_id WHERE b.apk_key=? AND json_extract(i.spec,'$.source_run')||':'||json_extract(i.spec,'$.apk_index')=b.apk_key AND (?='gameplay' OR json_extract(a.result,'$.capability')='device.install') ORDER BY a.rowid DESC LIMIT 1",
      apkKey,
      optionalText(task['kind']),
    );
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
    return text(
      this.database.one('SELECT task_id FROM task_aliases WHERE alias_id=?', taskId)?.['task_id'] ??
        taskId,
    );
  }

  private task(taskId: string): Fields {
    const row = this.database.one('SELECT spec FROM tasks WHERE id=?', taskId);
    if (!row) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Task not found');
    return parsed(row['spec']);
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
      const previous = this.database.one('SELECT id,spec FROM tasks WHERE submission_key=?', key);
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
      if (this.database.one('SELECT 1 FROM tasks WHERE id=?', taskId) || existsSync(directory))
        continue;
      mkdirSync(directory);
      this.database.execute(
        'INSERT INTO tasks VALUES(?,?,?,?)',
        taskId,
        key,
        JSON.stringify(spec),
        new Date().toISOString(),
      );
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
      if (this.database.one('SELECT 1 FROM runs WHERE id=?', runId) || existsSync(path)) continue;
      mkdirSync(path);
      const timestamp = new Date().toISOString();
      const mode = text(params['mode']);
      this.database.execute(
        'INSERT INTO runs VALUES(?,?,?,?,?,0,?,?)',
        runId,
        taskId,
        mode,
        'queued',
        'intake',
        timestamp,
        timestamp,
      );
      this.database.execute('INSERT INTO run_inputs VALUES(?,?)', runId, JSON.stringify(spec));
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
    this.database.execute(
      'UPDATE runs SET status=?,stage=?,updated_at=? WHERE id=?',
      status,
      stage,
      new Date().toISOString(),
      runId,
    );
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
    const rows = this.database.all(
      'SELECT * FROM device_bindings WHERE instance_id=? OR apk_key=?',
      instance,
      apkKey,
    );
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
    this.database.execute(
      'INSERT INTO device_bindings VALUES(?,?,?,?)',
      instance,
      apkKey,
      index,
      runId,
    );
    this.event(runId, 'device.reserved', {
      instance_id: instance,
      instance_index: index,
      apk_key: apkKey,
    });
  }

  private releaseDevice(runId: string, apply: boolean): Fields {
    this.checkCleanup();
    if (this.database.one("SELECT 1 FROM runs WHERE status='running' LIMIT 1"))
      throw new FrameworkError(ErrorCode.RUN_BUSY);
    const binding = this.database.one('SELECT * FROM device_bindings WHERE run_id=?', runId);
    if (!binding) throw new FrameworkError(ErrorCode.NOT_FOUND, 'Reservation not found');
    const unresolved = this.database.all(
      "SELECT a.run_id,a.action_id,a.status FROM actions a JOIN run_inputs i ON i.run_id=a.run_id WHERE json_extract(a.result,'$.instance_id')=? AND json_extract(i.spec,'$.source_run')||':'||json_extract(i.spec,'$.apk_index')=? AND a.status IN ('prepared','unknown','executed')",
      text(binding['instance_id']),
      text(binding['apk_key']),
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
      this.database.execute('DELETE FROM device_bindings WHERE run_id=?', runId);
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
    const binding = this.database.one(
      'SELECT apk_key FROM device_bindings WHERE instance_id=?',
      instance,
    );
    const task = this.input(runId);
    const apkKey = `${String(task['source_run'])}:${String(task['apk_index'])}`;
    if (
      !binding ||
      binding['apk_key'] !== apkKey ||
      !['device_preparation', 'gameplay'].includes(text(task['kind']))
    )
      throw new FrameworkError(ErrorCode.DEVICE_BINDING_CONFLICT);
    const count = integer(
      this.database.one('SELECT COUNT(*) AS count FROM actions WHERE run_id=?', runId)?.['count'],
    );
    if (params['max_actions'] != null && count >= integer(params['max_actions']))
      throw new FrameworkError(ErrorCode.BUDGET_EXHAUSTED);
    const pending = this.database.one(
      "SELECT 1 FROM actions a JOIN run_inputs i ON i.run_id=a.run_id WHERE json_extract(a.result,'$.instance_id')=? AND json_extract(i.spec,'$.source_run')||':'||json_extract(i.spec,'$.apk_index')=? AND a.status IN ('prepared','unknown','executed')",
      instance,
      apkKey,
    );
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
    this.database.execute(
      'INSERT INTO actions VALUES(?,?,?,?)',
      runId,
      actionId,
      'prepared',
      JSON.stringify(details),
    );
    this.event(runId, 'action.intent', { action_id: actionId, ...details }, { span_id: spanId });
    return actionId;
  }

  private action(runId: string, params: Fields, dispatch: boolean): void {
    const actionId = text(params['action_id']);
    const row = this.database.one(
      'SELECT status,result FROM actions WHERE run_id=? AND action_id=?',
      runId,
      actionId,
    );
    if (!row) throw new FrameworkError(ErrorCode.INVALID_STATE);
    const previous = text(row['status']);
    const details = parsed(row['result']);
    if (dispatch) {
      const run = this.run(runId);
      if (run['status'] !== 'running') throw new FrameworkError(ErrorCode.INVALID_STATE);
      if (run['cancel_requested']) throw new FrameworkError(ErrorCode.CANCELLED);
      if (previous !== 'prepared') throw new FrameworkError(ErrorCode.INVALID_STATE);
      this.database.execute(
        "UPDATE actions SET status='unknown' WHERE run_id=? AND action_id=?",
        runId,
        actionId,
      );
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
    this.database.execute(
      'UPDATE actions SET status=?,result=? WHERE run_id=? AND action_id=?',
      status,
      JSON.stringify(result),
      runId,
      actionId,
    );
    this.event(
      runId,
      'action.result',
      { action_id: actionId, from: previous, status, ...result },
      { span_id: text(details['span_id']) },
    );
  }

  private deleteTasks(taskIds: string[]): void {
    const selected = new Set(taskIds);
    const ownedRuns = new Set(
      this.database
        .all('SELECT id,task_id FROM runs')
        .filter((row) => selected.has(text(row['task_id'])))
        .map((row) => text(row['id'])),
    );
    for (const row of this.database.all(
      'SELECT r.task_id,i.spec FROM runs r JOIN run_inputs i ON i.run_id=r.id',
    )) {
      if (selected.has(text(row['task_id']))) continue;
      const spec = parsed(row['spec']);
      if (
        (typeof spec['source_run'] === 'string' && ownedRuns.has(spec['source_run'])) ||
        (typeof spec['prepared_run'] === 'string' && ownedRuns.has(spec['prepared_run']))
      )
        throw new FrameworkError(ErrorCode.CLEANUP_DEPENDENCY);
    }
    const encoded = JSON.stringify(taskIds);
    this.database.execute(
      'DELETE FROM exploration_records WHERE task_id IN (SELECT value FROM json_each(?))',
      encoded,
    );
    const runs = 'SELECT id FROM runs WHERE task_id IN (SELECT value FROM json_each(?))';
    for (const table of [
      'events',
      'sessions',
      'actions',
      'device_bindings',
      'gameplay_progress',
      'run_inputs',
    ]) {
      this.database.execute(`DELETE FROM ${table} WHERE run_id IN (${runs})`, encoded);
    }
    this.database.execute(`DELETE FROM runs WHERE id IN (${runs})`, encoded);
    const aliases = this.database
      .all(
        'SELECT alias_id FROM task_aliases WHERE task_id IN (SELECT value FROM json_each(?))',
        encoded,
      )
      .map((row) => text(row['alias_id']));
    this.database.execute(
      'DELETE FROM task_aliases WHERE task_id IN (SELECT value FROM json_each(?))',
      encoded,
    );
    this.database.execute(
      'DELETE FROM tasks WHERE id IN (SELECT value FROM json_each(?))',
      JSON.stringify([...taskIds, ...aliases]),
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
        this.deleteTasks(strings(params['task_ids']));
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
          this.database.execute(
            'INSERT OR REPLACE INTO sessions VALUES(?,?,?)',
            runId,
            role,
            threadId,
          );
          this.event(runId, 'ai.session', { role, thread_id: threadId });
          break;
        }
        case 'cancel':
          if (!['succeeded', 'failed', 'cancelled'].includes(text(this.run(runId)['status']))) {
            this.database.execute('UPDATE runs SET cancel_requested=1 WHERE id=?', runId);
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
            ['intake', 'game'].includes(text(run['mode'])) &&
            this.database.one(
              "SELECT 1 FROM events WHERE run_id=? AND kind='intake.accepted'",
              runId,
            )
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
          this.database.execute(
            'INSERT INTO gameplay_progress VALUES(?,?,?) ON CONFLICT(workspace_key) DO UPDATE SET run_id=excluded.run_id,data=excluded.data',
            key,
            runId,
            JSON.stringify(data),
          );
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
