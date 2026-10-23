import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { delimiter, isAbsolute, join } from 'node:path';
import { once } from 'node:events';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';

export async function executable(name: string): Promise<string> {
  const directories = isAbsolute(name) ? [''] : (process.env['PATH'] ?? '').split(delimiter);
  const extensions = process.platform === 'win32' && !name.endsWith('.exe') ? ['', '.exe'] : [''];
  for (const directory of directories) {
    for (const extension of extensions) {
      const candidate = directory ? join(directory, name + extension) : name + extension;
      try {
        await access(candidate, constants.X_OK);
        if ((await stat(candidate)).isFile()) return candidate;
      } catch {
        continue;
      }
    }
  }
  throw new FrameworkError(ErrorCode.CODEX_UNAVAILABLE);
}

export async function start(
  command: string,
  arguments_: string[],
  cwd: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<ChildProcessWithoutNullStreams> {
  const child = spawn(command, arguments_, {
    cwd,
    env: environment,
    windowsHide: true,
    stdio: 'pipe',
  });
  await once(child, 'spawn');
  return child;
}

export async function terminate(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) return;
  if (process.platform === 'win32') {
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore',
    });
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        killer.kill();
        resolve();
      }, 5_000);
      killer.once('error', () => {
        clearTimeout(timer);
        resolve();
      });
      killer.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
  child.kill();
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new FrameworkError(ErrorCode.TIMEOUT)), 5_000);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
    if (child.exitCode !== null || child.signalCode !== null) {
      clearTimeout(timer);
      resolve();
    }
  });
}
