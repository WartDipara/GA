import check from '../../../var/cache/typescript/generated/workspace-view-validator.js';
import checkHistory from '../../../var/cache/typescript/generated/history-view-validator.js';
import checkSearch from '../../../var/cache/typescript/generated/history-search-validator.js';
import type { ContractMap } from './contracts.js';

export type WorkspaceView = ContractMap['workspace-view'];
export type HistoryView = ContractMap['history-view'];
export type HistorySearch = ContractMap['history-search'];

export function workspaceView(value: unknown): WorkspaceView | null {
  return check(value) ? value : null;
}

export function historyView(value: unknown): HistoryView | null {
  return checkHistory(value) ? value : null;
}

export function historySearch(value: unknown): HistorySearch | null {
  return checkSearch(value) ? value : null;
}
