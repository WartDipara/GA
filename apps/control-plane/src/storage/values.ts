import { randomUUID } from 'node:crypto';
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
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

export function within(root: string, path: string): string {
  const base = realpathSync(root);
  const target = resolve(base, path);
  const check = (candidate: string): void => {
    const suffix = relative(base, candidate);
    if (isAbsolute(suffix) || suffix === '..' || suffix.startsWith(`..${sep}`)) {
      throw new FrameworkError(ErrorCode.INVALID_PATH);
    }
  };
  check(target);
  let existing = target;
  while (!existsSync(existing)) existing = dirname(existing);
  check(realpathSync(existing));
  return target;
}

export function atomicJson(path: string, value: unknown): void {
  atomicText(path, `${JSON.stringify(value, null, 2)}\n`);
}

export function atomicText(path: string, value: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${uid('tmp')}`;
  const descriptor = openSync(temporary, 'wx', 0o600);
  try {
    try {
      writeFileSync(descriptor, value, 'utf8');
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    renameSync(temporary, path);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

const sensitiveName =
  /^(?:authorization|password|passwd|secret|api[_-]?key|(?:access_|refresh_)?token|cookie|set-cookie)$/i;
const suffixes = [
  'password',
  'passwd',
  'secret',
  'apikey',
  'accesstoken',
  'refreshtoken',
  'authorization',
  'cookie',
];
const inlineSecret =
  /((?:password|passwd|api[_-]?key|access_token|refresh_token|token|secret|authorization|cookie)["']?\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&]+)/gi;

export function redact(value: unknown): unknown {
  if (Array.isArray(value)) {
    const values = value.map(redact);
    return values;
  }
  if (value !== null && typeof value === 'object') {
    const fields = object(value);
    const entries = Object.entries(fields).map(([key, item]) => {
      const normalized = key.toLowerCase().replaceAll(/[^a-z]/g, '');
      const sensitive =
        sensitiveName.test(key) || suffixes.some((suffix) => normalized.endsWith(suffix));
      const entry: [string, unknown] = [key, sensitive ? '[REDACTED]' : redact(item)];
      return entry;
    });
    const result: Fields = Object.fromEntries(entries);
    return result;
  }
  if (typeof value !== 'string') return value;
  try {
    const decoded: unknown = JSON.parse(value);
    if (decoded !== null && typeof decoded === 'object') return JSON.stringify(redact(decoded));
  } catch {
    // Plain text is redacted below.
  }
  return value
    .replaceAll(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]')
    .replaceAll(inlineSecret, '$1[REDACTED]');
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
