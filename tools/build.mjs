import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { Ajv2020 } from 'ajv/dist/2020.js';

const theme = JSON.parse(await readFile('config/ui-theme.json', 'utf8'));
const themeSchema = JSON.parse(await readFile('contracts/v1/ui-theme.schema.json', 'utf8'));
const validateTheme = new Ajv2020().compile(themeSchema);
if (!validateTheme(theme)) throw new Error('Invalid UI theme configuration');

await build({
  entryPoints: {
    trace_ui: 'packages/trace-ui/src/main.tsx',
    history_search: 'packages/trace-ui/src/history-worker.ts',
  },
  outdir: 'var/cache/typescript',
  bundle: true,
  platform: 'browser',
  target: 'es2022',
  format: 'iife',
  minify: true,
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'eof',
});
const properties = (values) =>
  Object.entries(values)
    .map(([name, value]) => `--${name}:${value};`)
    .join('');
const sharedTokens = { ...theme.tokens };
sharedTokens['font-body'] ??= sharedTokens['font-sans'];
sharedTokens['font-sans'] ??= sharedTokens['font-body'];
sharedTokens['font-display'] ??= sharedTokens['font-body'];
if (theme.appearance) {
  sharedTokens['default-font-scale'] = String(
    theme.appearance.font_size / theme.appearance.design_font_size,
  );
  sharedTokens['default-font-min'] =
    `${theme.appearance.font_size * theme.appearance.minimum_text_ratio}px`;
}
let tokens = `:root{${properties(sharedTokens)}}`;
for (const [mode, values] of Object.entries(theme.themes)) {
  const selector = `${mode === theme.default_mode ? ':root,' : ''}:root[data-theme="${mode}"]`;
  tokens += `${selector}{color-scheme:${mode};${properties(values)}}`;
}
const styles = await readFile('var/cache/typescript/trace_ui.css', 'utf8');
await writeFile('var/cache/typescript/trace_ui.css', `${tokens}\n${styles}`);

await build({
  entryPoints: {
    agent_codex: 'plugins/agent_codex/main.ts',
    control_plane: 'apps/control-plane/src/main.ts',
  },
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
  'config/ui-theme.json',
  'plugins/agent_codex/plugin.toml',
  'src/game_automation/errors.py',
  'var/cache/typescript/agent_codex.mjs',
  'var/cache/typescript/control_plane.mjs',
  'var/cache/typescript/trace_ui.js',
  'var/cache/typescript/trace_ui.css',
  'var/cache/typescript/history_search.js',
];
async function sources(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory() && entry.name !== '__pycache__') await sources(path);
    else if (/\.(tsx?|css|mjs|py|json)$/.test(entry.name)) files.push(path);
  }
}
for (const directory of ['apps', 'packages', 'plugins/agent_codex', 'tools'])
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
