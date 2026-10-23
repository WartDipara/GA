import { spawnSync } from 'node:child_process';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { compile } from 'json-schema-to-typescript';

const directory = resolve('var/cache/typescript/generated');
await mkdir(directory, { recursive: true });
const schemas = {};
const imports = [];
const entries = [];
for (const file of (await readdir('contracts/v1')).sort()) {
  if (!file.endsWith('.schema.json')) continue;
  const name = file.slice(0, -'.schema.json'.length);
  const type = name.split('-').map((part) => part[0].toUpperCase() + part.slice(1)).join('');
  const schema = JSON.parse(await readFile(`contracts/v1/${file}`, 'utf8'));
  const source = await compile({ ...schema, title: type }, type, {
    bannerComment: '// Generated from contracts/v1; do not edit.',
    unknownAny: true,
    style: { printWidth: 100, singleQuote: true },
  });
  await writeFile(`${directory}/${name}.ts`, source);
  schemas[name] = schema;
  imports.push(`import type { ${type} } from './${name}.js';`);
  entries.push(`  '${name}': ${type};`);
}
const map = `${imports.join('\n')}\n\nexport interface ContractMap {\n${entries.join('\n')}\n}\n`;
await writeFile(`${directory}/contracts.ts`, map);
await writeFile(`${directory}/schemas.ts`, `export const schemas = ${JSON.stringify(schemas)};\n`);

const exported = spawnSync('uv', ['run', 'python', 'tools/export-error-codes.py'], {
  encoding: 'utf8', windowsHide: true,
});
if (exported.status !== 0) throw new Error('Cannot generate framework error codes');
const errors = JSON.parse(exported.stdout);
const members = Object.entries(errors).map(([name, value]) => `  ${name} = ${JSON.stringify(value.code)},`);
const messages = Object.entries(errors).map(([name, value]) => `  [ErrorCode.${name}]: ${JSON.stringify(value.message)},`);
const source = `// Generated from the authoritative framework ErrorCode; do not edit.\n`
  + `export enum ErrorCode {\n${members.join('\n')}\n}\n`
  + `export const errorMessages: Record<ErrorCode, string> = {\n${messages.join('\n')}\n};\n`;
await writeFile(`${directory}/errors.ts`, source);
