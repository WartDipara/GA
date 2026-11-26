import { and, eq, gt, inArray, isNotNull, ne, or, sql, type SQL } from 'drizzle-orm';
import type { NodeSQLiteDatabase } from 'drizzle-orm/node-sqlite';
import { decode, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { insertionOrder } from './sqlite-expressions.js';
import { explorationRecords as records } from './tables.js';
import { parsed } from '../utilities/values.js';

type Entry = ContractMap['exploration-record'];
type Kind = Entry['kind'];
type RecordOf<K extends Kind> = Extract<Entry, { kind: K }>;

interface Selection {
  parent?: string;
  fromPage?: string;
  key?: string;
  actionId?: string;
  statuses?: readonly string[];
  observationId?: string;
  limit?: number;
  needsLedgerReview?: boolean;
  unscanned?: boolean;
  eligible?: boolean;
  excludeId?: string;
}

// Match the fixed expressions in schema.sql so SQLite can use its expression indexes.
const parent = sql<string>`json_extract(${records.data}, '$.parent_id')`;
const status = sql<string>`json_extract(${records.data}, '$.status')`;
const key = sql<string>`json_extract(${records.data}, '$.data.key')`;
const action = sql<string>`json_extract(${records.data}, '$.data.action_id')`;
const fromPage = sql<string>`json_extract(${records.data}, '$.data.from_page')`;
const observation = sql<string>`json_extract(${records.data}, '$.data.observation_id')`;
const everVerified = sql<number>`json_extract(${records.data}, '$.data.ever_verified')`;
const scanComplete = sql<number>`json_extract(${records.data}, '$.data.scan_complete')`;
const quarantined = sql<number>`coalesce(json_extract(${records.data}, '$.data.supervision.quarantined'), 0)`;
const rowOrder = insertionOrder(records);

export class ExplorationRepository {
  constructor(private readonly db: NodeSQLiteDatabase) {}

  // Resolve a committed record without allocating or decoding its siblings.
  get<K extends Kind>(workspace: string, kind: K, id: string): RecordOf<K> | undefined {
    const row = this.db.select({ data: records.data }).from(records)
      .where(and(eq(records.workspace_key, workspace), eq(records.kind, kind), eq(records.id, id))).get();
    return row ? this.decodeRecord(kind, row.data) : undefined;
  }

  // Preserve first-seen order for existing target priorities and report previews.
  list<K extends Kind>(workspace: string, kind: K, options: Selection = {}): RecordOf<K>[] {
    const query = this.db.select({ data: records.data }).from(records)
      .where(this.filter(workspace, kind, options)).orderBy(rowOrder);
    const rows = options.limit === undefined ? query.all() : query.limit(options.limit).all();
    return rows.map((row) => this.decodeRecord(kind, row.data));
  }

  // Completion and navigation only need to know whether qualifying work exists.
  exists(workspace: string, kind: Kind, options: Selection): boolean {
    const match = this.db.select({ id: records.id }).from(records)
      .where(this.filter(workspace, kind, options)).limit(1).get();
    return match !== undefined;
  }

  // Keyset batches allow an integrity sweep to update rows without skipping or holding the catalog.
  *review<K extends 'element' | 'edge'>(
    workspace: string, kind: K, actionId?: string,
  ): Generator<RecordOf<K>> {
    const options: Selection = kind === 'element'
      ? { needsLedgerReview: true } : { statuses: ['verified'] };
    if (actionId !== undefined) options.actionId = actionId;
    let after: number | null = null;
    for (;;) {
      const rows = this.db.select({ position: rowOrder, data: records.data }).from(records)
        .where(and(this.filter(workspace, kind, options),
          after === null ? undefined : gt(rowOrder, after)))
        .orderBy(rowOrder).limit(64).all();
      if (!rows.length) return;
      for (const row of rows) {
        after = row.position;
        yield this.decodeRecord(kind, row.data);
      }
    }
  }

  private filter(workspace: string, kind: Kind, options: Selection): SQL | undefined {
    return and(
      eq(records.workspace_key, workspace), eq(records.kind, kind),
      options.parent === undefined ? undefined : eq(parent, options.parent),
      options.fromPage === undefined ? undefined : eq(fromPage, options.fromPage),
      options.key === undefined ? undefined : eq(key, options.key),
      options.actionId === undefined ? undefined : eq(action, options.actionId),
      options.observationId === undefined ? undefined : eq(observation, options.observationId),
      options.statuses ? inArray(status, options.statuses) : undefined,
      options.excludeId === undefined ? undefined : ne(records.id, options.excludeId),
      options.eligible ? eq(quarantined, 0) : undefined,
      options.unscanned ? eq(scanComplete, 0) : undefined,
      options.needsLedgerReview
        ? or(eq(status, 'verified'), eq(everVerified, 1), and(eq(status, 'in_progress'), isNotNull(action)))
        : undefined,
    );
  }

  private decodeRecord<K extends Kind>(kind: K, data: string): RecordOf<K> {
    const record = decode('exploration-record', parsed(data));
    const matches = (value: Entry): value is RecordOf<K> => value.kind === kind;
    if (!matches(record)) throw new FrameworkError(ErrorCode.INVALID_STATE, 'Exploration record kind differs from its index');
    return record;
  }
}
