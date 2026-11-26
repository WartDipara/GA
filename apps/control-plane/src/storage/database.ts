import { DatabaseSync } from 'node:sqlite';
import { eq } from 'drizzle-orm';
import { drizzle, type NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { gameplayProgress, runInputs, runs, taskAliases, tasks } from './tables.js';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { counters, schema } from './schema.js';
import { parsed } from './values.js';
import { performance } from 'node:perf_hooks';
import { WriterHealth } from '../observability/writer-health.js';

export class Database {
  readonly health = new WriterHealth();
  private readonly connection: DatabaseSync;
  readonly orm: NodeSQLiteDatabase;
  private depth = 0;
  failure: FrameworkError | undefined;

  constructor(readonly directory: string) {
    mkdirSync(directory, { recursive: true });
    this.connection = new DatabaseSync(join(directory, 'state.sqlite'), { timeout: 5_000 });
    this.orm = drizzle({ client: this.connection });
    this.connection.exec(
      'PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;',
    );
    const version = this.connection.prepare('PRAGMA user_version').get()?.['user_version'];
    if (typeof version !== 'number' || ![0, 1, 2, 3, 4, 5].includes(version)) {
      this.connection.close();
      throw new FrameworkError(ErrorCode.SCHEMA_VERSION);
    }
    this.connection.exec(schema);
    if (version < 4) this.upgrade(version);
    if (version < 5) {
      if (version === 4) {
        const backup = join(this.directory, 'migrations/exploration/state-before-v5.sqlite');
        mkdirSync(dirname(backup), { recursive: true });
        if (!existsSync(backup)) this.connection.prepare('VACUUM INTO ?').run(backup);
      }
      this.connection.exec('PRAGMA user_version=5');
    }
    if (!this.connection.prepare("SELECT 1 FROM sqlite_master WHERE name='event_usage'").get()) {
      this.connection.exec(counters);
    }
  }

  transaction<T>(operation: () => T): T {
    if (this.failure) throw this.failure;
    if (this.depth > 0) return operation();
    this.depth++;
    const started = performance.now();
    let failed = true;
    try {
      this.connection.exec('BEGIN IMMEDIATE');
      const result = operation();
      const committing = performance.now();
      let commitFailed = true;
      try {
        this.connection.exec('COMMIT');
        commitFailed = false;
      } finally {
        this.health.record('commit', committing, commitFailed);
      }
      failed = false;
      return result;
    } catch (error) {
      try {
        if (this.connection.isTransaction) this.connection.exec('ROLLBACK');
      } catch {
        this.failure = new FrameworkError(ErrorCode.TRACE_UNAVAILABLE);
      }
      if (error instanceof FrameworkError) throw error;
      this.failure ??= new FrameworkError(ErrorCode.TRACE_UNAVAILABLE, undefined, false, {
        cause: error,
      });
      throw this.failure;
    } finally {
      this.health.record('transaction', started, failed);
      this.depth--;
    }
  }

  compact(): void {
    for (const statement of [
      'PRAGMA wal_checkpoint(TRUNCATE)',
      'VACUUM',
      'PRAGMA wal_checkpoint(TRUNCATE)',
    ]) {
      if (statement === 'VACUUM') this.connection.exec(statement);
      else if (this.connection.prepare(statement).get()?.['busy'] !== 0)
        throw new FrameworkError(ErrorCode.CLEANUP_FAILED);
    }
  }

  close(): void {
    this.connection.close();
  }

  private upgrade(version: number): void {
    if (version !== 0) {
      const backup = join(this.directory, 'migrations/task-layout/state-before-upgrade.sqlite');
      mkdirSync(dirname(backup), { recursive: true });
      if (!existsSync(backup)) this.connection.prepare('VACUUM INTO ?').run(backup);
    }
    const specifications = this.orm.select({ id: tasks.id, spec: tasks.spec }).from(tasks).all();
    const taskSpecs = new Map(specifications.map((row) => [row.id, parsed(row.spec)]));
    const executions = this.orm.select({ id: runs.id, taskId: runs.task_id }).from(runs).all();
    const owners = new Map(executions.map((row) => [row.id, text(row.taskId)]));
    const parents = new Map<string, string>();
    for (const [taskId, spec] of taskSpecs) {
      const source = spec['prepared_run'] ?? spec['source_run'];
      const parent = typeof source === 'string' ? owners.get(source) : undefined;
      if (parent && parent !== taskId) parents.set(taskId, parent);
    }
    const root = (taskId: string): string => {
      const visited = new Set<string>();
      while (parents.has(taskId)) {
        if (visited.has(taskId))
          throw new FrameworkError(ErrorCode.INVALID_STATE, 'Task lineage contains a cycle');
        visited.add(taskId);
        taskId = text(parents.get(taskId));
      }
      return taskId;
    };
    this.transaction(() => {
      for (const [runId, taskId] of owners) {
        const input = { run_id: runId, spec: JSON.stringify(object(taskSpecs.get(taskId))) };
        this.orm.insert(runInputs).values(input).run();
        this.orm
          .update(runs)
          .set({ task_id: root(taskId) })
          .where(eq(runs.id, runId))
          .run();
      }
      for (const taskId of parents.keys()) {
        const alias = { alias_id: taskId, task_id: root(taskId) };
        this.orm.insert(taskAliases).values(alias).run();
      }
      const progress = this.orm
        .select({ key: gameplayProgress.workspace_key, runId: gameplayProgress.run_id })
        .from(gameplayProgress)
        .all();
      for (const row of progress) {
        const taskId = root(text(owners.get(row.runId)));
        this.orm
          .update(gameplayProgress)
          .set({ workspace_key: `${taskId}:${row.key}` })
          .where(eq(gameplayProgress.workspace_key, row.key))
          .run();
      }
      this.connection.exec('PRAGMA user_version=4');
    });
  }
}
