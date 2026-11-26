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
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { uid } from './values.js';

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
