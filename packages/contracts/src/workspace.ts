import check from '../../../var/cache/typescript/generated/workspace-validator.js';
import type { ContractMap } from './contracts.js';

export type WorkspaceView = ContractMap['workspace-view'];

export function workspaceView(value: unknown): WorkspaceView | null {
  return check(value) ? value : null;
}
