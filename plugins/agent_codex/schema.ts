import { object } from '../../src/control_plane/contracts.js';

export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export function jsonValue(value: unknown): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    const converted = value.map(jsonValue);
    return converted;
  }
  const result: { [key: string]: JsonValue } = {};
  for (const [key, item] of Object.entries(object(value))) result[key] = jsonValue(item);
  return result;
}

export function strictSchema(schema: Record<string, unknown>): Record<string, JsonValue> {
  const result = object(jsonValue(schema));
  if (schema['type'] === 'object') {
    const properties = object(schema['properties'] ?? {});
    const required: unknown = schema['required'] ?? [];
    if (!Array.isArray(required)) throw new TypeError('Schema required must be an array');
    const converted: Record<string, JsonValue> = {};
    for (const [name, child] of Object.entries(properties)) {
      const value = strictSchema(object(child));
      converted[name] = required.includes(name) ? value : { anyOf: [value, { type: 'null' }] };
    }
    result['properties'] = converted;
    result['required'] = Object.keys(properties);
    result['additionalProperties'] = false;
  }
  if (schema['items']) result['items'] = strictSchema(object(schema['items']));
  for (const name of ['anyOf', 'oneOf', 'allOf']) {
    const members = schema[name];
    if (Array.isArray(members))
      result[name] = members.map((child: unknown) => strictSchema(object(child)));
  }
  const converted: Record<string, JsonValue> = {};
  for (const [key, item] of Object.entries(result)) converted[key] = jsonValue(item);
  return converted;
}

export function restoreOptional(value: unknown, schema: Record<string, unknown>): unknown {
  const alternatives = schema['anyOf'];
  if (Array.isArray(alternatives)) {
    for (const alternative of alternatives) {
      const candidate = object(alternative);
      if (
        value !== null &&
        typeof value === 'object' &&
        !Array.isArray(value) &&
        candidate['type'] === 'object'
      )
        return restoreOptional(value, candidate);
    }
  }
  if (Array.isArray(value)) {
    const restored = value.map((item: unknown) =>
      restoreOptional(item, object(schema['items'] ?? {})),
    );
    return restored;
  }
  if (value !== null && typeof value === 'object') {
    const properties = object(schema['properties'] ?? {});
    const required: unknown = schema['required'] ?? [];
    if (!Array.isArray(required)) throw new TypeError('Schema required must be an array');
    const restored: Record<string, unknown> = {};
    for (const [name, item] of Object.entries(value)) {
      if (item !== null || required.includes(name)) {
        restored[name] = restoreOptional(item, object(properties[name] ?? {}));
      }
    }
    return restored;
  }
  return value;
}
