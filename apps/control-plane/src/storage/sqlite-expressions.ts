import { getTableName, sql, type SQLWrapper } from 'drizzle-orm';
import type { AnySQLiteTable } from 'drizzle-orm/sqlite-core';

export function jsonField(column: SQLWrapper, path: string) {
  return sql<string | number | null>`json_extract(${column}, ${path})`;
}

export function insertionOrder(table: AnySQLiteTable) {
  return sql<number>`${sql.identifier(getTableName(table))}.rowid`;
}

export function jsonValues(values: readonly string[]) {
  return sql<string>`(select value from json_each(${JSON.stringify(values)}))`;
}
