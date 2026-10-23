import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { once } from 'node:events';
import { createServer, type Socket } from 'node:net';
import { resolve, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { performance } from 'node:perf_hooks';
import { decode, object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { Host } from './kernel/host.js';
import { Peer } from '@game-automation/plugin-sdk/rpc';
import { Store } from './storage/store.js';
import { atomicJson, type Fields } from './storage/values.js';
import { Execution } from './workflow/execution.js';

const root = resolve(process.argv[2] ?? '.');
const expectedVersion = readFileSync(join(root, '.node-version'), 'utf8').trim();
if (process.version !== `v${expectedVersion}`)
  throw new FrameworkError(ErrorCode.NODE_RUNTIME_MISMATCH);
if (process.platform !== 'win32')
  throw new FrameworkError(
    ErrorCode.UNSUPPORTED_PLATFORM,
    'The writer lease requires Windows named pipes',
  );
const name = createHash('sha256').update(root.toLowerCase()).digest('hex');
const pipe = `\\\\.\\pipe\\game-auto-writer-${name}`;
const fingerprint = createHash('sha256')
  .update(readFileSync(process.argv[1] ?? ''))
  .digest('hex');
const token = randomBytes(32).toString('hex');
const lease = createServer((socket) => {
  socket.destroy();
});

// Acquire an OS-owned singleton before opening SQLite. A losing starter never becomes a writer.
const acquired = once(lease, 'listening');
lease.listen({ path: pipe, exclusive: true });
await acquired;
lease.on('error', (error: Error) => {
  // Ownership errors must reach the fatal boundary, never a settled startup promise.
  throw error;
});
const store = new Store(root);
const calls = new Map<string, AbortController>();
const executions = new Map<string, AbortController>();
const cancelled = new Set<string>();
const sockets = new Set<Socket>();
let idle: NodeJS.Timeout | undefined;
let stopping = false;

function stopWhenIdle(): void {
  if (sockets.size || calls.size || executions.size || stopping) return;
  clearTimeout(idle);
  idle = setTimeout(() => {
    if (sockets.size || calls.size || executions.size || stopping) return;
    stopping = true;
    // Close the database before releasing the singleton lease.
    store.database.close();
    server.close();
    lease.close();
  }, 2_000);
}

const server = createServer((socket) => {
  if (stopping) {
    socket.destroy();
    return;
  }
  clearTimeout(idle);
  socket.setTimeout(10_000, () => {
    socket.destroy();
  });
  sockets.add(socket);
  const owned = new Set<string>();
  const ownedExecutions = new Set<string>();
  let authenticated = false;
  const handler = async (method: string, params: Fields): Promise<unknown> => {
    if (!authenticated) {
      if (method !== 'hello' || params['token'] !== token)
        throw new FrameworkError(ErrorCode.CAPABILITY_DENIED);
      if (params['fingerprint'] !== fingerprint)
        throw new FrameworkError(
          ErrorCode.RUNTIME_CHANGED,
          'The active control plane uses another build; finish its clients before retrying',
        );
      authenticated = true;
      socket.setTimeout(0);
      const result = { protocol_version: '1.0', fingerprint, pid: process.pid };
      return result;
    }
    if (method === 'host.cancel') {
      const id = text(params['call_id']);
      const active = calls.get(id);
      if (active) active.abort();
      else {
        if (cancelled.size >= 1024) throw new FrameworkError(ErrorCode.MESSAGE_TOO_LARGE);
        cancelled.add(id);
      }
      return null;
    }
    if (method === 'status') {
      const status = {
        pid: process.pid,
        active_calls: calls.size,
        active_executions: executions.size,
      };
      return status;
    }
    if (method.startsWith('execution.')) {
      decode('execution-request', { operation: method.slice('execution.'.length), ...params });
      if (executions.size) throw new FrameworkError(ErrorCode.RUN_BUSY);
      const id = `execution_${randomBytes(16).toString('hex')}`;
      const controller = new AbortController();
      executions.set(id, controller);
      ownedExecutions.add(id);
      const tool = async (operation: string, runId: string, input: Fields): Promise<unknown> => {
        const request = { operation, run_id: runId, input };
        return peer.request('execution.tool', request, 0);
      };
      const execution = new Execution(store, tool, controller.signal, drainCalls);
      try {
        if (method === 'execution.create') {
          await drainCalls();
          return await execution.create(
            text(params['task_id']),
            text(params['mode']),
            params['spec'] == null ? null : object(params['spec']),
          );
        }
        if (method === 'execution.run') {
          await execution.recover();
          await execution.repairReports();
          const runId = text(params['run_id']);
          if (store.input(runId)['workflow_run'])
            throw new FrameworkError(
              ErrorCode.WORKFLOW_INVALID,
              'Resume the owning workflow instead of its child',
            );
          return await execution.run(
            runId,
            params['source_file'] == null ? null : text(params['source_file']),
          );
        }
        if (method === 'execution.recover') {
          await execution.recover();
          return null;
        }
        if (method === 'execution.report') {
          await execution.recover();
          if (params['pending'] === true) return await execution.repairReports();
          const receipts = [await execution.publish(text(params['run_id']))];
          return receipts;
        }
        throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND, method);
      } finally {
        executions.delete(id);
        ownedExecutions.delete(id);
        stopWhenIdle();
      }
    }
    if (method === 'host.execute') {
      const callId = text(params['call_id']);
      if (calls.has(callId))
        throw new FrameworkError(ErrorCode.INVALID_STATE, 'A host request cannot be replayed');
      const controller = new AbortController();
      if (cancelled.has(callId)) {
        controller.abort();
        cancelled.delete(callId);
      }
      calls.set(callId, controller);
      owned.add(callId);
      try {
        store.checkCleanup();
        const runId = text(params['run_id']);
        if (store.run(runId)['status'] !== 'running')
          throw new FrameworkError(
            ErrorCode.CANCELLED,
            'The execution no longer accepts plugin calls',
          );
        const eventId = `kernel_${callId}`;
        if (store.database.one('SELECT 1 FROM events WHERE event_id=?', eventId)) {
          throw new FrameworkError(ErrorCode.INVALID_STATE, 'A host request cannot be replayed');
        }
        const accepted = { call_id: callId, operation: text(params['operation']) };
        store.event(runId, 'kernel.request.accepted', accepted, {
          event_id: eventId,
          request_id: callId,
        });
        const host = new Host(store, runId, object(params['configuration']), controller.signal);
        let result: unknown = null;
        let error: unknown = null;
        try {
          result = await host.execute(
            text(params['operation']),
            object(params['input']),
            object(params['context']),
          );
        } catch (failure) {
          error = frameworkError(failure).details();
        }
        const response = {
          result,
          error,
          artifacts: [...host.artifacts],
          halted: !!host.failure || !!store.database.failure,
        };
        return response;
      } finally {
        calls.delete(callId);
        owned.delete(callId);
        stopWhenIdle();
      }
    }
    if (method === 'storage.prepare_cleanup') {
      if (calls.size || executions.size)
        throw new FrameworkError(
          ErrorCode.RUN_BUSY,
          'Wait for interrupted plugin calls to stop before cleanup',
        );
      return null;
    }
    if (method === 'storage.quiesce') {
      if (executions.size) throw new FrameworkError(ErrorCode.RUN_BUSY);
      await drainCalls();
      return null;
    }
    if (method === 'storage.release_device' && (calls.size || executions.size))
      throw new FrameworkError(ErrorCode.RUN_BUSY);
    if (method.startsWith('storage.')) {
      const command = { method: method.slice('storage.'.length), input: params };
      return store.accept(command);
    }
    throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND, method);
  };
  const peer = new Peer(socket, socket, handler, undefined, false, false);
  const disconnect = (): void => {
    for (const id of owned) calls.get(id)?.abort();
    for (const id of ownedExecutions)
      executions.get(id)?.abort(new FrameworkError(ErrorCode.PREVIOUS_PROCESS_EXITED));
    sockets.delete(socket);
    stopWhenIdle();
  };
  socket.once('close', disconnect);
  void peer.done.catch(() => {
    socket.destroy();
  });
});

async function drainCalls(): Promise<void> {
  for (const controller of calls.values()) controller.abort();
  const deadline = performance.now() + 120_000;
  while (calls.size) {
    if (performance.now() >= deadline)
      throw new FrameworkError(ErrorCode.TIMEOUT, 'Interrupted plugin processes have not stopped');
    await delay(50);
  }
}
server.on('error', (error: Error) => {
  store.database.failure ??= new FrameworkError(ErrorCode.TRACE_UNAVAILABLE, undefined, false, {
    cause: error,
  });
  for (const controller of calls.values()) controller.abort();
  for (const controller of executions.values()) controller.abort(error);
  for (const socket of sockets) socket.destroy();
  stopWhenIdle();
});
await new Promise<void>((resolveServer) => {
  server.listen({ host: '127.0.0.1', port: 0, exclusive: true }, resolveServer);
});
const address = server.address();
if (address === null || typeof address === 'string')
  throw new FrameworkError(ErrorCode.INTERNAL_ERROR);
const endpoint = { port: address.port, token, fingerprint, pid: process.pid };
atomicJson(join(root, 'var/cache/control-plane/endpoint.json'), endpoint);
stopWhenIdle();
