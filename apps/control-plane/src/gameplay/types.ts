import type { ContractMap } from '@game-automation/contracts';

export type Action = ContractMap['gameplay-action'];
export type Decision = ContractMap['gameplay-decision'];
export type Progress = ContractMap['gameplay-progress'];
export type Facts = ContractMap['perception-result'];
export type Observation = ContractMap['device-capture-result'] & {
  observation_id: string;
  evidence_ref: string;
};
export type Routine = ContractMap['gameplay-routine'];
export type Pending = NonNullable<Progress['pending']>;
export type Coordinates = ContractMap['planner-image-result'];
