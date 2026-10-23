import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { within } from './values.js';

export class FileUsage {
  private files = new Map<string, number>();
  private bytes = 0;
  private reconciled: number | null = null;
  constructor(readonly directory: string) {}

  async track(reference: string): Promise<void> {
    const path = within(this.directory, reference);
    const info = await lstat(path).catch((error: unknown) => {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return null;
      throw error;
    });
    if (info?.isSymbolicLink()) throw new FrameworkError(ErrorCode.INVALID_PATH);
    if (info?.isDirectory()) {
      for (const entry of await readdir(path)) await this.track(join(path, entry));
      return;
    }
    const current = info?.size ?? 0;
    this.bytes += current - (this.files.get(path) ?? 0);
    if (current) this.files.set(path, current);
    else this.files.delete(path);
  }

  async total(): Promise<number> {
    if (this.reconciled === null || performance.now() - this.reconciled >= 300000) {
      this.files = new Map();
      this.bytes = 0;
      await this.track(this.directory);
      this.reconciled = performance.now();
    }
    return this.bytes;
  }
}
