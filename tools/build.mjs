import { build } from 'esbuild';

await build({
  entryPoints: { contracts: 'src/control_plane/contracts.ts' },
  outdir: 'var/cache/typescript',
  outExtension: { '.js': '.mjs' },
  bundle: true,
  platform: 'node',
  target: 'node22.19',
  format: 'esm',
  legalComments: 'eof',
});
