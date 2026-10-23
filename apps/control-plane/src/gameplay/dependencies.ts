import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import { within } from '../storage/values.js';
import type { Runtime } from './runtime.js';

async function digest(path: string): Promise<string> {
  const bytes = await readFile(path);
  return createHash('sha256').update(bytes).digest('hex');
}

export async function verifyPlanningDependencies(runtime: Runtime): Promise<void> {
  runtime.check();
  const snapshot = runtime.store
    .events(runtime.runId, { kinds: ['run.snapshot'], limit: 1 })
    .at(-1);
  const manifestPath = within(runtime.directory, 'manifest.json');
  try {
    if (!snapshot || (await digest(manifestPath)) !== snapshot.payload['manifest_sha256'])
      throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
  } catch (error) {
    if (error instanceof FrameworkError) throw error;
    throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED, 'Pinned manifest is unavailable');
  }
  for (const [path, expected] of Object.entries(object(runtime.manifest['source_hashes']))) {
    try {
      if ((await digest(path)) !== expected) throw new FrameworkError(ErrorCode.RUNTIME_CHANGED);
    } catch (error) {
      if (error instanceof FrameworkError) throw error;
      throw new FrameworkError(ErrorCode.RUNTIME_CHANGED, 'Pinned implementation is unavailable');
    }
  }
  const roots = object(runtime.manifest['resource_roots']);
  const hashes = object(runtime.manifest['files']);
  for (const kind of ['skills', 'contracts']) {
    const root = text(roots[kind]);
    const prefix = `${kind}/`;
    const expected = new Map(
      Object.entries(hashes)
        .filter(([name]) => name.startsWith(prefix))
        .map(([name, hash]) => [name.slice(prefix.length), hash]),
    );
    if (!expected.size) throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
    const folders =
      kind === 'skills'
        ? [...new Set([...expected.keys()].map((name) => name.split('/')[0]))].map((name) =>
            within(root, text(name)),
          )
        : [root];
    const observed = new Set<string>();
    const scan = async (folder: string): Promise<void> => {
      for (const entry of await readdir(folder, { withFileTypes: true })) {
        if (entry.isSymbolicLink()) throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
        const path = within(root, join(folder, entry.name));
        if (entry.isDirectory()) await scan(path);
        else {
          const name = relative(root, path).replaceAll('\\', '/');
          if ((await digest(path)) !== expected.get(name))
            throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
          observed.add(name);
        }
      }
    };
    try {
      for (const folder of folders) await scan(folder);
    } catch (error) {
      if (error instanceof FrameworkError) throw error;
      throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED, 'Pinned resources are unavailable');
    }
    if (observed.size !== expected.size) throw new FrameworkError(ErrorCode.SNAPSHOT_CHANGED);
  }
  runtime.check();
}
