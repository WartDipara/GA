import { DatabaseSync, type SQLInputValue } from 'node:sqlite';
import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { counters, schema } from './schema.js';
import { parsed, type Fields } from './values.js';

export class Database {
  private readonly connection: DatabaseSync;
  private depth = 0;
  failure: FrameworkError | undefined;

  constructor(readonly directory: string) {
    mkdirSync(directory, { recursive: true });
    this.connection = new DatabaseSync(join(directory, 'state.sqlite'), { timeout: 5_000 });
    this.connection.exec(
      'PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;',
    );
    const version = this.one('PRAGMA user_version')?.['user_version'];
    if (typeof version !== 'number' || ![0, 1, 2, 3, 4, 5].includes(version)) {
      this.connection.close();
      throw new FrameworkError(ErrorCode.SCHEMA_VERSION);
    }
    this.connection.exec(schema);
    if (version < 4) this.upgrade(version);
    if (version < 5) this.connection.exec('PRAGMA user_version=5');
    if (!this.one("SELECT 1 FROM sqlite_master WHERE name='event_usage'")) {
      this.connection.exec(counters);
    }
  }

  all(sql: string, ...parameters: SQLInputValue[]): Fields[] {
    const rows = this.connection.prepare(sql).all(...parameters);
    return rows;
  }

  one(sql: string, ...parameters: SQLInputValue[]): Fields | undefined {
    return this.connection.prepare(sql).get(...parameters);
  }

  execute(sql: string, ...parameters: SQLInputValue[]): void {
    this.connection.prepare(sql).run(...parameters);
  }

  transaction<T>(operation: () => T): T {
    if (this.failure) throw this.failure;
    if (this.depth > 0) return operation();
    this.depth++;
    try {
      this.connection.exec('BEGIN IMMEDIATE');
      const result = operation();
      this.connection.exec('COMMIT');
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
      else if (this.one(statement)?.['busy'] !== 0)
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
      if (!existsSync(backup)) this.execute('VACUUM INTO ?', backup);
    }
    const tasks = new Map(
      this.all('SELECT id,spec FROM tasks').map((row) => [text(row['id']), parsed(row['spec'])]),
    );
    const owners = new Map(
      this.all('SELECT id,task_id FROM runs').map((row) => [text(row['id']), text(row['task_id'])]),
    );
    const parents = new Map<string, string>();
    for (const [taskId, spec] of tasks) {
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
        this.execute(
          'INSERT INTO run_inputs VALUES(?,?)',
          runId,
          JSON.stringify(object(tasks.get(taskId))),
        );
        this.execute('UPDATE runs SET task_id=? WHERE id=?', root(taskId), runId);
      }
      for (const taskId of parents.keys())
        this.execute('INSERT INTO task_aliases VALUES(?,?)', taskId, root(taskId));
      for (const row of this.all('SELECT workspace_key,run_id FROM gameplay_progress')) {
        const taskId = root(text(owners.get(text(row['run_id']))));
        this.execute(
          'UPDATE gameplay_progress SET workspace_key=? WHERE workspace_key=?',
          `${taskId}:${text(row['workspace_key'])}`,
          text(row['workspace_key']),
        );
      }
      this.connection.exec('PRAGMA user_version=4');
    });
  }
}
