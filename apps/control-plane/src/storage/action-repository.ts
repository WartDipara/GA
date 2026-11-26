import { and, count, desc, eq, inArray, sql } from 'drizzle-orm';
import type { NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { insertionOrder, jsonField } from './sqlite-expressions.js';
import { actions, deviceBindings, runInputs, runs } from './tables.js';

const pending = ['prepared', 'unknown', 'executed'];
const instance = jsonField(actions.result, '$.instance_id');
const sourceRun = jsonField(runInputs.spec, '$.source_run');
const apkIndex = jsonField(runInputs.spec, '$.apk_index');
const apk = sql<string>`${sourceRun} || ':' || ${apkIndex}`;
const reference = { run_id: actions.run_id, action_id: actions.action_id, status: actions.status };

export class ActionRepository {
  constructor(private readonly db: NodeSQLiteDatabase) {}

  latest(apkKey: string, gameplay: boolean) {
    return this.db
      .select({ ...reference, result: actions.result })
      .from(actions)
      .innerJoin(deviceBindings, eq(instance, deviceBindings.instance_id))
      .innerJoin(runInputs, eq(runInputs.run_id, actions.run_id))
      .where(
        and(
          eq(deviceBindings.apk_key, apkKey),
          eq(apk, deviceBindings.apk_key),
          gameplay
            ? undefined
            : inArray(jsonField(actions.result, '$.capability'), [
                'device.install',
                'device.uninstall',
              ]),
        ),
      )
      .orderBy(desc(insertionOrder(actions)))
      .limit(1)
      .get();
  }

  private workspacePending(instanceId: string, apkKey: string) {
    return this.db
      .select(reference)
      .from(actions)
      .innerJoin(runInputs, eq(runInputs.run_id, actions.run_id))
      .where(and(eq(instance, instanceId), eq(apk, apkKey), inArray(actions.status, pending)));
  }

  unresolvedForWorkspace(instanceId: string, apkKey: string) {
    return this.workspacePending(instanceId, apkKey).all();
  }

  hasUnresolvedForWorkspace(instanceId: string, apkKey: string): boolean {
    return Boolean(this.workspacePending(instanceId, apkKey).limit(1).get());
  }

  unresolvedForRun(runId: string) {
    return this.db
      .select({ action_id: actions.action_id, status: actions.status })
      .from(actions)
      .where(and(eq(actions.run_id, runId), inArray(actions.status, pending)))
      .orderBy(insertionOrder(actions))
      .all();
  }

  hasUnknown(runId: string): boolean {
    return Boolean(
      this.db
        .select({ action_id: actions.action_id })
        .from(actions)
        .where(and(eq(actions.run_id, runId), eq(actions.status, 'unknown')))
        .limit(1)
        .get(),
    );
  }

  total(runId: string): number {
    const row = this.db
      .select({ count: count() })
      .from(actions)
      .where(eq(actions.run_id, runId))
      .get();
    return row?.count ?? 0;
  }

  countsByStatus(runId: string) {
    return this.db
      .select({ status: actions.status, count: count() })
      .from(actions)
      .where(eq(actions.run_id, runId))
      .groupBy(actions.status)
      .all();
  }

  forTask(taskId: string, actionId: string) {
    return this.db
      .select({ ...reference, result: actions.result })
      .from(actions)
      .innerJoin(runs, eq(runs.id, actions.run_id))
      .where(and(eq(runs.task_id, taskId), eq(actions.action_id, actionId)))
      .get();
  }

  byId(runId: string, actionId: string) {
    return this.db
      .select({ status: actions.status, result: actions.result })
      .from(actions)
      .where(and(eq(actions.run_id, runId), eq(actions.action_id, actionId)))
      .get();
  }

  insert(action: typeof actions.$inferInsert): void {
    this.db.insert(actions).values(action).run();
  }

  update(runId: string, actionId: string, status: string, result?: string): void {
    const fields = result === undefined ? { status } : { status, result };
    this.db
      .update(actions)
      .set(fields)
      .where(and(eq(actions.run_id, runId), eq(actions.action_id, actionId)))
      .run();
  }
}
