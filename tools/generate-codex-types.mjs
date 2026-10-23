import { spawnSync } from 'node:child_process';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const configuration = await readFile('config/application.toml', 'utf8');
const version = /\[codex\][\s\S]*?\nversion = "([^"]+)"/.exec(configuration)?.[1];
const command = /\[codex\][\s\S]*?\nexecutable = "([^"]+)"/.exec(configuration)?.[1];
if (!version || !command) throw new Error('Missing pinned Codex runtime');
const actual = spawnSync(command, ['--version'], { encoding: 'utf8', windowsHide: true });
if (actual.status !== 0 || actual.stdout.trim() !== `codex-cli ${version}`) {
  throw new Error('Codex must match the pinned version before generating provider types');
}
const generated = spawnSync(
  command,
  ['app-server', 'generate-ts', '--out', 'var/cache/typescript/codex'],
  {
    encoding: 'utf8',
    windowsHide: true,
  },
);
if (generated.status !== 0) throw new Error('Cannot generate the pinned provider protocol');

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
await normalize('var/cache/typescript/codex');
