import { appendFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { frameworkError } from './errors.js';
import { redact } from './storage/values.js';

function fatal(error: unknown): never {
  const directory = join(resolve(process.argv[2] ?? '.'), 'var/logs');
  const details = {
    error: frameworkError(error).details(),
    stack: error instanceof Error ? (error.stack ?? null) : null,
  };
  try {
    mkdirSync(directory, { recursive: true });
    appendFileSync(join(directory, 'development.log'), `${JSON.stringify(redact(details))}\n`);
  } finally {
    // Process exit also releases the OS writer lease; unknown actions remain durable.
    process.exit(1);
  }
}

process.once('uncaughtException', fatal);
process.once('unhandledRejection', fatal);
await import('./service.js').catch(fatal);
