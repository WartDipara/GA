import { object } from '@game-automation/contracts';
import type { Fields } from './values.js';

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
