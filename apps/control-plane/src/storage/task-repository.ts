import { eq, inArray } from 'drizzle-orm';
import type { NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { jsonValues } from './sqlite-expressions.js';
import {
  actions,
  deviceBindings,
  events,
  explorationRecords,
  gameplayProgress,
  runInputs,
  runs,
  sessions,
  taskAliases,
  tasks,
} from './tables.js';
import { parsed } from '../utilities/values.js';

export class TaskRepository {
  constructor(private readonly db: NodeSQLiteDatabase) {}

  deleteOwned(taskIds: string[]): void {
    const selected = new Set(taskIds);
    const taskKeys = jsonValues(taskIds);
    const owned = this.db.select({ id: runs.id }).from(runs).where(inArray(runs.task_id, taskKeys));
    const ownedRuns = new Set(owned.all().map((row) => row.id));
    const specifications = this.db
      .select({ task_id: runs.task_id, spec: runInputs.spec })
      .from(runs)
      .innerJoin(runInputs, eq(runInputs.run_id, runs.id))
      .all();
    for (const row of specifications) {
      if (selected.has(text(row.task_id))) continue;
      const spec = parsed(row.spec);
      if (
        (typeof spec['source_run'] === 'string' && ownedRuns.has(spec['source_run'])) ||
        (typeof spec['prepared_run'] === 'string' && ownedRuns.has(spec['prepared_run']))
      )
        throw new FrameworkError(ErrorCode.CLEANUP_DEPENDENCY);
    }
    this.db.delete(explorationRecords).where(inArray(explorationRecords.task_id, taskKeys)).run();
    for (const table of [events, sessions, actions, deviceBindings, gameplayProgress, runInputs]) {
      this.db.delete(table).where(inArray(table.run_id, owned)).run();
    }
    this.db.delete(runs).where(inArray(runs.task_id, taskKeys)).run();
    const aliases = this.db
      .select({ id: taskAliases.alias_id })
      .from(taskAliases)
      .where(inArray(taskAliases.task_id, taskKeys))
      .all()
      .map((row) => row.id);
    this.db.delete(taskAliases).where(inArray(taskAliases.task_id, taskKeys)).run();
    const allKeys = jsonValues([...taskIds, ...aliases]);
    this.db.delete(tasks).where(inArray(tasks.id, allKeys)).run();
  }
}
