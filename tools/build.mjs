import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';

await build({
  entryPoints: { agent_codex: 'plugins/agent_codex/main.ts' },
  outdir: 'var/cache/typescript',
  outExtension: { '.js': '.mjs' },
  bundle: true,
  platform: 'node',
  target: 'node22.19',
  format: 'esm',
  legalComments: 'eof',
});

const files = [
  'package.json',
  'package-lock.json',
  'tsconfig.json',
  'eslint.config.mjs',
  '.node-version',
  'config/application.toml',
  'plugins/agent_codex/plugin.toml',
  'src/game_automation/errors.py',
  'var/cache/typescript/agent_codex.mjs',
];
async function sources(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory() && entry.name !== '__pycache__') await sources(path);
    else if (/\.(ts|mjs|py)$/.test(entry.name)) files.push(path);
  }
}
for (const directory of ['src/control_plane', 'plugins/agent_codex', 'tools'])
  await sources(directory);
for (const file of await readdir('contracts/v1')) {
  if (file.endsWith('.schema.json')) files.push(`contracts/v1/${file}`);
}
const hashes = {};
for (const file of files.sort())
  hashes[file] = createHash('sha256')
    .update(await readFile(file))
    .digest('hex');
await writeFile(
  'var/cache/typescript/build.json',
  `${JSON.stringify({ files: hashes }, null, 2)}\n`,
);
