import { Ajv2020 } from 'ajv/dist/2020.js';
import type { ContractMap } from '../../../var/cache/typescript/generated/contracts.js';
import { schemas } from '../../../var/cache/typescript/generated/schemas.js';
import { ErrorCode, FrameworkError } from './errors.js';

export type { ContractMap };

export function schema(name: keyof ContractMap): unknown {
  return schemas[name];
}
const validator = new Ajv2020({ strict: false, allErrors: false, validateFormats: false });

export function decode<K extends keyof ContractMap>(name: K, value: unknown): ContractMap[K] {
  const check = validator.compile<ContractMap[K]>(schemas[name]);
  if (!check(value)) {
    const path = check.errors?.[0]?.instancePath ?? '';
    throw new FrameworkError(ErrorCode.INVALID_CONTRACT, `${name}: invalid field at ${path}`);
  }
  return value;
}

export function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new FrameworkError(ErrorCode.INVALID_PROTOCOL, 'Expected an object');
  }
  const result: Record<string, unknown> = Object.fromEntries(Object.entries(value));
  return result;
}

export function text(value: unknown): string {
  if (typeof value !== 'string') throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
  return value;
}
