import { and, desc, eq, exists } from 'drizzle-orm';
import type { NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { eventField } from './event-repository.js';
import { insertionOrder, jsonField } from './sqlite-expressions.js';
import { events, runInputs, runs } from './tables.js';

export class WorkflowRepository {
  constructor(private readonly db: NodeSQLiteDatabase) {}

  running() {
    return this.db
      .select({ id: runs.id })
      .from(runs)
      .where(eq(runs.status, 'running'))
      .orderBy(insertionOrder(runs))
      .all();
  }

  hasRunning(): boolean {
    return Boolean(
      this.db.select({ id: runs.id }).from(runs).where(eq(runs.status, 'running')).limit(1).get(),
    );
  }

  gameplayChildren(workflowId: string) {
    return this.db
      .select({ id: runs.id })
      .from(runs)
      .innerJoin(runInputs, eq(runInputs.run_id, runs.id))
      .where(
        and(eq(runs.mode, 'gameplay'), eq(jsonField(runInputs.spec, '$.workflow_run'), workflowId)),
      )
      .orderBy(desc(insertionOrder(runs)))
      .all();
  }

  lifecycleRuns() {
    const lifecycle = this.db
      .select({ seq: events.seq })
      .from(events)
      .where(and(eq(events.run_id, runs.id), eq(events.kind, 'gameplay.lifecycle.started')));
    return this.db
      .select({ run_id: runs.id })
      .from(runs)
      .where(and(eq(runs.mode, 'gameplay'), exists(lifecycle)))
      .all();
  }

  hasPreviousClose(scope: {
    taskId: string;
    sourceRun: string;
    apkIndex: number;
    instanceId: string;
    target: string;
    sha256: string;
  }): boolean {
    return Boolean(
      this.db
        .select({ seq: events.seq })
        .from(events)
        .innerJoin(runs, eq(runs.id, events.run_id))
        .innerJoin(runInputs, eq(runInputs.run_id, events.run_id))
        .where(
          and(
            eq(runs.task_id, scope.taskId),
            eq(events.kind, 'gameplay.lifecycle.started'),
            eq(eventField('operation'), 'close'),
            eq(eventField('apkHash'), scope.sha256),
            eq(jsonField(runInputs.spec, '$.source_run'), scope.sourceRun),
            eq(jsonField(runInputs.spec, '$.apk_index'), scope.apkIndex),
            eq(jsonField(runInputs.spec, '$.instance_id'), scope.instanceId),
            eq(jsonField(runInputs.spec, '$.target'), scope.target),
          ),
        )
        .limit(1)
        .get(),
    );
  }
}
