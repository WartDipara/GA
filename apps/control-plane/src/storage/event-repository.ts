import { and, asc, desc, eq, gt, inArray, lte, max } from 'drizzle-orm';
import type { NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { jsonField } from './sqlite-expressions.js';
import { events, runs } from './tables.js';

const paths = {
  capability: '$.payload.capability',
  action: '$.payload.action_id',
  observation: '$.payload.observation_id',
  subgoal: '$.payload.subgoal',
  itemType: '$.payload.item.type',
  workspace: '$.payload.workspace_key',
  element: '$.payload.record.id',
  elementStatus: '$.payload.record.status',
  elementAction: '$.payload.record.data.action_id',
  status: '$.payload.status',
  outcomeObservation: '$.payload.result.observation_id',
  outcomeRun: '$.payload.result.observation_run',
  operation: '$.payload.operation',
  apkHash: '$.payload.sha256',
};

type EventField = keyof typeof paths;

export function eventField(field: EventField) {
  return jsonField(events.data, paths[field]);
}

interface EventReference {
  field: EventField;
  value: string;
}

export interface EventQuery {
  after?: number;
  span?: string;
  kinds?: string[];
  limit?: number;
  through?: number;
  capability?: string;
  reference?: EventReference;
}

const evidence = { seq: events.seq, data: events.data };

export class EventRepository {
  constructor(private readonly db: NodeSQLiteDatabase) {}

  range(runId: string, query: EventQuery) {
    const where = and(
      eq(events.run_id, runId),
      gt(events.seq, query.after ?? 0),
      query.through === undefined ? undefined : lte(events.seq, query.through),
      query.span ? eq(events.span_id, query.span) : undefined,
      query.kinds?.length ? inArray(events.kind, query.kinds) : undefined,
      query.capability ? eq(eventField('capability'), query.capability) : undefined,
      query.reference ? eq(eventField(query.reference.field), query.reference.value) : undefined,
    );
    const plan = this.db.select(evidence).from(events).where(where);
    const rows = query.limit
      ? plan.orderBy(desc(events.seq)).limit(query.limit).all().reverse()
      : plan.orderBy(asc(events.seq)).all();
    return rows;
  }

  watermark(runId: string): number {
    const row = this.db
      .select({ seq: max(events.seq) })
      .from(events)
      .where(eq(events.run_id, runId))
      .get();
    return row?.seq ?? 0;
  }

  byId(id: string) {
    return this.db.select({ data: events.data }).from(events).where(eq(events.event_id, id)).get();
  }

  append(event: typeof events.$inferInsert): void {
    this.db.insert(events).values(event).run();
  }

  hasKind(runId: string, kind: string): boolean {
    return Boolean(
      this.db
        .select({ seq: events.seq })
        .from(events)
        .where(and(eq(events.run_id, runId), eq(events.kind, kind)))
        .limit(1)
        .get(),
    );
  }

  taskEvents(taskId: string, kind: string) {
    return this.db
      .select(evidence)
      .from(events)
      .innerJoin(runs, eq(events.run_id, runs.id))
      .where(and(eq(runs.task_id, taskId), eq(events.kind, kind)))
      .orderBy(asc(events.seq))
      .all();
  }

  latestDecision(taskId: string, observationId: string) {
    return this.db
      .select(evidence)
      .from(events)
      .innerJoin(runs, eq(events.run_id, runs.id))
      .where(
        and(
          eq(runs.task_id, taskId),
          eq(events.kind, 'gameplay.decision'),
          eq(eventField('observation'), observationId),
        ),
      )
      .orderBy(desc(events.seq))
      .limit(1)
      .get();
  }

  latestExplorationRejection(workspaceKey: string) {
    return this.db
      .select(evidence)
      .from(events)
      .where(
        and(
          eq(events.kind, 'exploration.proposal.rejected'),
          eq(eventField('workspace'), workspaceKey),
        ),
      )
      .orderBy(desc(events.seq))
      .limit(1)
      .get();
  }

  explorationBinding(runId: string, workspaceKey: string, elementId: string, actionId: string) {
    return this.db
      .select(evidence)
      .from(events)
      .where(
        and(
          eq(events.run_id, runId),
          eq(events.kind, 'exploration.element.updated'),
          eq(eventField('workspace'), workspaceKey),
          eq(eventField('element'), elementId),
          eq(eventField('elementStatus'), 'in_progress'),
          eq(eventField('elementAction'), actionId),
        ),
      )
      .orderBy(asc(events.seq))
      .limit(1)
      .get();
  }

  observation(runId: string, observationId: string, taskId: string) {
    return this.db
      .select(evidence)
      .from(events)
      .innerJoin(runs, eq(events.run_id, runs.id))
      .where(
        and(
          eq(events.run_id, runId),
          eq(runs.task_id, taskId),
          eq(events.kind, 'gameplay.observation'),
          eq(eventField('observation'), observationId),
        ),
      )
      .orderBy(asc(events.seq))
      .limit(1)
      .get();
  }

  actionDispatches(runId: string, actionId: string) {
    return this.db
      .select({ seq: events.seq })
      .from(events)
      .where(
        and(
          eq(events.run_id, runId),
          eq(events.kind, 'action.dispatched'),
          eq(eventField('action'), actionId),
        ),
      )
      .all();
  }

  verifiedAction(runId: string, actionId: string, observationId: string, observationRun: string) {
    return this.db
      .select(evidence)
      .from(events)
      .where(
        and(
          eq(events.run_id, runId),
          eq(events.kind, 'action.result'),
          eq(eventField('action'), actionId),
          eq(eventField('status'), 'verified'),
          eq(eventField('outcomeObservation'), observationId),
          eq(eventField('outcomeRun'), observationRun),
        ),
      )
      .orderBy(desc(events.seq))
      .limit(1)
      .get();
  }

  reportRuns() {
    return this.db
      .selectDistinct({ run_id: events.run_id })
      .from(events)
      .where(eq(events.kind, 'report.requested'))
      .orderBy(asc(events.run_id))
      .all();
  }
}
