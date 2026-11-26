import { randomUUID } from 'node:crypto';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';

export type Fields = Record<string, unknown>;

export function uid(prefix: string): string {
  return `${prefix}_${randomUUID().replaceAll('-', '')}`;
}

export function optionalText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value);
}

export function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new FrameworkError(ErrorCode.INVALID_PROTOCOL, 'Expected an integer');
  }
  return value;
}

export function strings(value: unknown): string[] {
  if (!Array.isArray(value)) throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
  const values = value.map(text);
  return values;
}

export function parsed(value: unknown): Fields {
  const decoded: unknown = JSON.parse(text(value));
  return object(decoded);
}

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const fields = object(value);
    const entries = Object.keys(fields)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(fields[key])}`);
    return `{${entries.join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}
