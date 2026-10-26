import { randomUUID } from 'node:crypto';
import type { Readable, Writable } from 'node:stream';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';

const MAX_MESSAGE = 8 * 1024 * 1024;
const MAX_PENDING = 64;
const MAX_WRITE_BYTES = 16 * 1024 * 1024;
type Message = Record<string, unknown>;
type RpcId = string | number;
type Pending = {
  resolve: (value: unknown) => void;
  reject: (error: FrameworkError) => void;
  timer: NodeJS.Timeout | undefined;
};
export type Observer = (direction: 'in' | 'out', message: Message) => Promise<void>;
export type Handler = (method: string, params: Message) => Promise<unknown>;

export class Peer {
  readonly done: Promise<void>;
  failure: FrameworkError | undefined;
  private readonly pending = new Map<RpcId, Pending>();
  private readonly handlers = new Set<Promise<void>>();
  private closing = false;
  private writes: Promise<void> = Promise.resolve();
  private queuedWriteBytes = 0;
  private queuedWrites = 0;

  constructor(
    private readonly reader: Readable,
    private readonly writer: Writable,
    private readonly handler: Handler,
    private readonly observer?: Observer,
    private readonly provider = false,
    private readonly reportExceptions = true,
  ) {
    this.writer.on('error', (error: Error) => {
      this.fail(error);
      this.reader.destroy();
    });
    this.done = this.read();
    void this.done.catch(() => undefined);
  }

  async request(method: string, params: Message, timeoutMs = 30_000): Promise<unknown> {
    if (this.pending.size >= MAX_PENDING) throw new FrameworkError(ErrorCode.RPC_BACKPRESSURE);
    const id = `rpc_${randomUUID().replaceAll('-', '')}`;
    const response = new Promise<unknown>((resolve, reject) => {
      const timer =
        timeoutMs === 0
          ? undefined
          : setTimeout(() => {
              this.pending.delete(id);
              reject(
                new FrameworkError(ErrorCode.TIMEOUT, `Timed out waiting for ${method}`, true),
              );
            }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    void response.catch(() => undefined);
    try {
      await this.send({ id, method, params });
      return await response;
    } finally {
      const pending = this.pending.get(id);
      if (pending) clearTimeout(pending.timer);
      this.pending.delete(id);
    }
  }

  async notify(method: string, params: Message): Promise<void> {
    await this.send({ method, params });
  }

  private async send(message: Message): Promise<void> {
    const envelope = this.provider ? message : { jsonrpc: '2.0', ...message };
    const bytes = Buffer.from(`${JSON.stringify(envelope)}\n`);
    if (bytes.length > MAX_MESSAGE) throw new FrameworkError(ErrorCode.MESSAGE_TOO_LARGE);
    if (this.queuedWriteBytes + bytes.length > MAX_WRITE_BYTES || this.queuedWrites >= MAX_PENDING)
      throw new FrameworkError(ErrorCode.RPC_BACKPRESSURE);
    this.queuedWriteBytes += bytes.length;
    this.queuedWrites++;
    const write = this.writes.then(async () => {
      if (this.failure) throw this.failure;
      if (this.closing) throw new FrameworkError(ErrorCode.CLOSED);
      await this.observer?.('out', envelope);
      await new Promise<void>((resolve, reject) => {
        this.writer.write(bytes, (error) => (error ? reject(error) : resolve()));
      });
    });
    this.writes = write.catch((error: unknown) => {
      this.fail(error);
    });
    try {
      await write;
    } finally {
      this.queuedWriteBytes -= bytes.length;
      this.queuedWrites--;
    }
  }

  private async read(): Promise<void> {
    let buffer = Buffer.alloc(0);
    try {
      for await (const chunk of this.reader) {
        const data: unknown = chunk;
        if (!Buffer.isBuffer(data)) throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
        buffer = Buffer.concat([buffer, data]);
        let newline = buffer.indexOf(10);
        while (newline >= 0) {
          if (newline > MAX_MESSAGE) throw new FrameworkError(ErrorCode.MESSAGE_TOO_LARGE);
          const parsed: unknown = JSON.parse(buffer.subarray(0, newline).toString('utf8'));
          buffer = buffer.subarray(newline + 1);
          const message = object(parsed);
          if (!this.provider && message['jsonrpc'] !== '2.0') {
            throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
          }
          await this.observer?.('in', message);
          this.receive(message);
          newline = buffer.indexOf(10);
        }
        if (buffer.length > MAX_MESSAGE) throw new FrameworkError(ErrorCode.MESSAGE_TOO_LARGE);
      }
      if (!this.closing && (this.pending.size > 0 || this.handlers.size > 0)) {
        throw new FrameworkError(ErrorCode.PROCESS_EXITED);
      }
    } catch (error) {
      if (!this.closing) {
        this.fail(error);
        throw this.failure ?? frameworkError(error);
      }
    }
  }

  private receive(message: Message): void {
    const id = message['id'];
    if ('method' in message) {
      if (id === undefined) return;
      if (typeof id !== 'string' && typeof id !== 'number') {
        throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
      }
      if (this.handlers.size >= MAX_PENDING) throw new FrameworkError(ErrorCode.RPC_BACKPRESSURE);
      const task = this.dispatch(text(message['method']), object(message['params'] ?? {}), id);
      this.handlers.add(task);
      void task.then(
        () => {
          this.handlers.delete(task);
        },
        (error: unknown) => {
          this.handlers.delete(task);
          this.fail(error);
          this.reader.destroy();
        },
      );
      return;
    }
    if (typeof id !== 'string' && typeof id !== 'number') {
      throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
    }
    const pending = this.pending.get(id);
    if (!pending) return;
    if ('error' in message) {
      const error = object(message['error']);
      const details = object(error['data'] ?? {});
      const code = Object.values(ErrorCode).find((item) => item === details['code']);
      pending.reject(
        new FrameworkError(
          code ?? ErrorCode.RPC_ERROR,
          text(error['message']),
          details['retryable'] === true,
        ),
      );
    } else if ('result' in message) {
      pending.resolve(message['result']);
    } else {
      throw new FrameworkError(ErrorCode.INVALID_PROTOCOL);
    }
    clearTimeout(pending.timer);
    this.pending.delete(id);
  }

  private async dispatch(method: string, params: Message, id: RpcId): Promise<void> {
    let response: Message;
    try {
      response = { id, result: await this.handler(method, params) };
    } catch (error) {
      const failure = frameworkError(error);
      if (!this.provider && this.reportExceptions) {
        const payload = {
          error: failure.details(),
          stack: error instanceof Error ? (error.stack ?? null) : null,
        };
        const acknowledged = object(
          await this.request('host.event', {
            kind: 'plugin.node.exception',
            payload,
            event_id: `evt_${randomUUID().replaceAll('-', '')}`,
          }),
        );
        if (acknowledged['persisted'] !== true)
          throw new FrameworkError(ErrorCode.TRACE_UNAVAILABLE);
      }
      response = { id, error: { code: -32000, message: failure.message, data: failure.details() } };
    }
    await this.send(response);
  }

  private fail(error: unknown): void {
    this.failure ??= frameworkError(error);
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(this.failure);
    }
    this.pending.clear();
  }

  async close(): Promise<void> {
    this.closing = true;
    this.fail(new FrameworkError(ErrorCode.CLOSED));
    this.reader.destroy();
    this.writer.destroy();
    await this.done.catch(() => undefined);
    await Promise.allSettled(this.handlers);
  }
}
