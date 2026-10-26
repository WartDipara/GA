import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const expected = readFileSync(join(root, '.node-version'), 'utf8').trim();
const python = join(
  root,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
if (process.versions.node !== expected || !existsSync(python)) {
  process.stderr.write(
    `Use Node ${expected} and run uv sync --locked before starting the viewer.\n`,
  );
  process.exitCode = 1;
} else {
  const args = [
    '-m',
    'game_automation.cli',
    '--project',
    root,
    'serve-trace',
    ...process.argv.slice(2),
  ];
  const child = spawn(python, args, { cwd: root, stdio: 'inherit', windowsHide: true });
  child.on('error', () => {
    process.stderr.write('The trace viewer could not be started.\n');
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    process.exitCode = code ?? (signal ? 130 : 1);
  });
  for (const signal of ['SIGINT', 'SIGTERM'])
    process.on(signal, () => {
      child.kill(signal);
    });
}
