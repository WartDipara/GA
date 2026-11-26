import { createHash, type BinaryLike } from 'node:crypto';
import { readFile } from 'node:fs/promises';

export function sha256(value: BinaryLike): string {
  return createHash('sha256').update(value).digest('hex');
}

export async function sha256File(path: string): Promise<string> {
  const contents = await readFile(path);
  return sha256(contents);
}

// Retain existing checkpoint fingerprints across the Python-to-TypeScript migration.
export function checkpointHash(value: unknown): string {
  const format = (item: unknown): string => {
    if (Array.isArray(item)) return `[${item.map(format).join(', ')}]`;
    if (item !== null && typeof item === 'object') {
      const fields = Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
      return `{${fields.map(([key, value]) => `${format(key)}: ${format(value)}`).join(', ')}}`;
    }
    return (JSON.stringify(item) ?? 'null').replace(
      /[\u007f-\uffff]/g,
      (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
    );
  };
  return sha256(format(value));
}
