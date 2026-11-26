import { integer, primaryKey, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const tasks = sqliteTable('tasks', {
  id: text().primaryKey(),
  submission_key: text().unique(),
  spec: text().notNull(),
  created_at: text(),
});

export const runs = sqliteTable('runs', {
  id: text().primaryKey(),
  task_id: text().references(() => tasks.id),
  mode: text().notNull(),
  status: text().notNull(),
  stage: text().notNull(),
  cancel_requested: integer().default(0),
  created_at: text(),
  updated_at: text(),
});

export const events = sqliteTable('events', {
  seq: integer().primaryKey({ autoIncrement: true }),
  event_id: text().unique().notNull(),
  run_id: text().references(() => runs.id),
  span_id: text(),
  kind: text().notNull(),
  data: text().notNull(),
});

export const sessions = sqliteTable(
  'sessions',
  {
    run_id: text(),
    role: text(),
    thread_id: text(),
  },
  (table) => [primaryKey({ columns: [table.run_id, table.role] })],
);

export const actions = sqliteTable(
  'actions',
  {
    run_id: text(),
    action_id: text(),
    status: text(),
    result: text(),
  },
  (table) => [primaryKey({ columns: [table.run_id, table.action_id] })],
);

export const deviceBindings = sqliteTable('device_bindings', {
  instance_id: text().primaryKey(),
  apk_key: text().unique().notNull(),
  instance_index: integer().notNull(),
  run_id: text()
    .unique()
    .notNull()
    .references(() => runs.id),
});

export const gameplayProgress = sqliteTable('gameplay_progress', {
  workspace_key: text().primaryKey(),
  run_id: text()
    .notNull()
    .references(() => runs.id),
  data: text().notNull(),
});

export const runInputs = sqliteTable('run_inputs', {
  run_id: text()
    .primaryKey()
    .references(() => runs.id),
  spec: text().notNull(),
});

export const taskAliases = sqliteTable('task_aliases', {
  alias_id: text()
    .primaryKey()
    .references(() => tasks.id),
  task_id: text()
    .notNull()
    .references(() => tasks.id),
});

export const explorationRecords = sqliteTable(
  'exploration_records',
  {
    workspace_key: text().notNull(),
    kind: text().notNull(),
    id: text().notNull(),
    task_id: text()
      .notNull()
      .references(() => tasks.id, { onDelete: 'cascade' }),
    run_id: text()
      .notNull()
      .references(() => runs.id),
    data: text().notNull(),
  },
  (table) => [primaryKey({ columns: [table.workspace_key, table.kind, table.id] })],
);

export const eventUsage = sqliteTable('event_usage', {
  run_id: text()
    .primaryKey()
    .references(() => runs.id, { onDelete: 'cascade' }),
  size_bytes: integer().notNull(),
});
