import { spawnSync } from 'node:child_process';
import {
  lstat, mkdir, mkdtemp, readdir, readFile, realpath, rename, rm, writeFile,
} from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const options = { encoding: 'utf8', windowsHide: true, timeout: 60_000 };
const readExecutable = [
  'import tomllib',
  'from pathlib import Path',
  'configuration = tomllib.loads(Path("config/application.toml").read_text(encoding="utf-8"))',
  'print(configuration["codex"]["executable"])',
].join('\n');
const configuration = spawnSync('uv', ['run', '--locked', 'python', '-c', readExecutable], options);
const command = configuration.stdout?.trim();
if (configuration.status !== 0 || !command) throw new Error('Missing Codex executable setting');
const detected = spawnSync(command, ['--version'], options);
const version = /^codex-cli (\d+\.\d+\.\d+(?:[-+][\w.-]+)?)$/.exec(detected.stdout?.trim() ?? '')?.[1];
if (detected.status !== 0 || !version) throw new Error('Cannot detect the installed Codex version');

async function normalize(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await normalize(path);
    else if (entry.name.endsWith('.ts')) {
      const source = await readFile(path, 'utf8');
      const esm = source.replace(/from "(\.[^"]+?)(?:\.js)?"/g, 'from "$1.js"');
      await writeFile(path, esm);
    }
  }
}

async function publishTypes(source, destination) {
  const filesystemBusy = new Set(['EACCES', 'EBUSY', 'EPERM']);
  for (let attempt = 0; ; attempt++) {
    try {
      await rename(source, destination);
      return;
    } catch (error) {
      if (process.platform !== 'win32' || !filesystemBusy.has(error.code)) throw error;
      if (attempt === 10) {
        throw new Error(
          'Cannot publish Codex types. Close programs holding files in the TypeScript cache and rerun the build.',
          { cause: error },
        );
      }
      // Windows file scanners can briefly hold generated files open after the provider exits.
      await delay(100 * (attempt + 1));
    }
  }
}

const cache = resolve('var/cache/typescript');
await mkdir(cache, { recursive: true });
if (await realpath(cache) !== cache) throw new Error('The TypeScript cache must be a local directory');
const generatedDir = join(cache, 'codex');
const next = await mkdtemp(join(cache, 'codex-types-'));
const removeDirectory = { recursive: true, maxRetries: 10, retryDelay: 100 };
try {
  const generated = spawnSync(command, ['app-server', 'generate-ts', '--out', next], options);
  if (generated.status !== 0) throw new Error('Cannot generate App Server types from installed Codex');
  await normalize(next);
  const prior = await lstat(generatedDir).catch((error) => {
    if (error.code !== 'ENOENT') throw error;
    return null;
  });
  if (prior) {
    if (!prior.isDirectory() || await realpath(generatedDir) !== generatedDir)
      throw new Error('The generated Codex directory must be inside the TypeScript cache');
    await rm(generatedDir, removeDirectory);
  }
  await publishTypes(next, generatedDir);
  await writeFile(join(cache, 'codex-version.json'), `${JSON.stringify({ version })}\n`);
  process.stdout.write(`Generated App Server types for Codex ${version}.\n`);
} finally {
  await rm(next, { ...removeDirectory, force: true });
}
