import { mkdirSync, statfsSync } from 'node:fs';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { integer } from './values.js';

export function requireDiskSpace(directory: string, required: number): void {
  let available: number;
  try {
    mkdirSync(directory, { recursive: true });
    const filesystem = statfsSync(directory);
    available = integer(filesystem.bsize * filesystem.bavail);
    if (available < 0) throw new Error('Invalid capacity');
  } catch (error) {
    throw new FrameworkError(ErrorCode.DISK_SPACE_UNAVAILABLE, undefined, false, { cause: error });
  }
  if (available < required)
    throw new FrameworkError(
      ErrorCode.DISK_SPACE_INSUFFICIENT,
      `Disk capacity is insufficient: ${required} bytes required, ${available} available`,
    );
}
