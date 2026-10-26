import { randomUUID } from 'node:crypto';
import { object, text } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Peer } from '@game-automation/plugin-sdk/rpc';
import { TraceBuffer } from './trace-buffer.js';

type Entry = { kind: string; payload: Record<string, unknown> };

export class ProviderEvents {
  threadId: string | null = null;
  turnId: string | null = null;
  turn: Record<string, unknown> | undefined;
  readonly messages = new Map<string, string>();
  summaryAvailable = false;
  usage: Record<string, unknown> | null = null;
  private failure: FrameworkError | undefined;
  private readonly buffer: TraceBuffer;
  private wireSequence = 0;
  private complete: (() => void) | undefined;
  private readonly completion = new Promise<void>((resolve) => {
    this.complete = resolve;
  });

  constructor(
    private readonly host: Peer,
    readonly batches: boolean,
  ) {
    this.buffer = new TraceBuffer(
      async (events) => {
        const result = object(await this.host.request('host.events', { events }));
        if (result['persisted'] !== true) throw new FrameworkError(ErrorCode.TRACE_UNAVAILABLE);
      },
      (error) => this.abort(error),
    );
  }

  abort(error: FrameworkError): void {
    this.failure = error;
    this.complete?.();
  }

  async emit(kind: string, payload: Record<string, unknown>): Promise<void> {
    await this.buffer.flush();
    const request = { kind, payload, event_id: `provider_${randomUUID().replaceAll('-', '')}` };
    const result = object(await this.host.request('host.event', request));
    if (result['persisted'] !== true) throw new FrameworkError(ErrorCode.TRACE_UNAVAILABLE);
  }

  private async persist(entries: Entry[], durable = true): Promise<void> {
    if (!this.batches) {
      for (const entry of entries) await this.emit(entry.kind, entry.payload);
      return;
    }
    const events = entries.map((entry) => ({
      ...entry,
      event_id: `provider_${randomUUID().replaceAll('-', '')}`,
    }));
    const source = events.find((entry) => entry.kind === 'provider.wire');
    if (source)
      for (const entry of events)
        if (['ai.progress', 'ai.reasoning_summary'].includes(entry.kind))
          entry.payload['source_event_id'] = source.event_id;
    await this.buffer.append(events, durable);
  }

  async finish(): Promise<void> {
    await this.buffer.flush();
    if (this.batches) await this.emit('ai.trace.buffer', this.buffer.stats());
  }

  async observe(direction: 'in' | 'out', message: Record<string, unknown>): Promise<void> {
    const entries: Entry[] = [
      {
        kind: 'provider.wire',
        payload: {
          direction,
          message,
          source_seq: ++this.wireSequence,
          received_at: new Date().toISOString(),
        },
      },
    ];
    let completed: Record<string, unknown> | undefined;
    try {
      if (direction === 'in' && typeof message['method'] === 'string') {
        const method = message['method'];
        const data = object(message['params'] ?? {});
        if (!data['threadId'] || !this.threadId || data['threadId'] === this.threadId) {
          if (method === 'item/started' || method === 'item/completed') {
            const item = object(data['item']);
            const tool = ['mcpToolCall', 'commandExecution', 'dynamicToolCall'].includes(
              text(item['type']),
            );
            const payload = {
              thread_id: data['threadId'],
              turn_id: data['turnId'],
              item_id: item['id'],
              item,
            };
            entries.push({
              kind: `ai.${tool ? 'tool' : 'item'}.${method.endsWith('started') ? 'started' : 'completed'}`,
              payload,
            });
            if (
              item['type'] === 'reasoning' &&
              Array.isArray(item['summary']) &&
              item['summary'].length
            ) {
              this.summaryAvailable = true;
            }
            if (
              method === 'item/completed' &&
              item['type'] === 'agentMessage' &&
              item['phase'] !== 'commentary'
            ) {
              this.messages.set(text(item['id']), text(item['text']));
            }
          } else if (
            method === 'item/agentMessage/delta' ||
            method === 'item/reasoning/summaryTextDelta'
          ) {
            const reasoning = method.includes('reasoning');
            entries.push({
              kind: reasoning ? 'ai.reasoning_summary' : 'ai.progress',
              payload: {
                thread_id: data['threadId'],
                turn_id: data['turnId'],
                item_id: data['itemId'],
                text: text(data['delta']),
              },
            });
            if (reasoning && data['delta']) this.summaryAvailable = true;
          } else if (method === 'turn/started') {
            this.turnId = text(object(data['turn'])['id']);
          } else if (method === 'thread/tokenUsage/updated') {
            this.usage = data['tokenUsage'] === null ? null : object(data['tokenUsage']);
            entries.push({ kind: 'ai.usage', payload: { usage: this.usage } });
          } else if (method === 'turn/completed') {
            const turn = object(data['turn']);
            if (data['threadId'] === this.threadId && turn['id'] === this.turnId) completed = turn;
          }
        }
      }
    } catch (error) {
      await this.persist(entries.slice(0, 1));
      throw error;
    }
    const streaming =
      direction === 'in' &&
      ['item/agentMessage/delta', 'item/reasoning/summaryTextDelta'].includes(
        String(message['method']),
      );
    await this.persist(entries, !streaming);
    if (completed) {
      this.turn = completed;
      this.complete?.();
    }
  }

  async wait(
    peer: Peer,
    timeoutMs: number,
    stderr: Promise<void>,
  ): Promise<Record<string, unknown>> {
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new FrameworkError(ErrorCode.TIMEOUT)), timeoutMs);
    });
    const stderrFailure = stderr.then(() => new Promise<never>(() => undefined));
    try {
      await Promise.race([this.completion, peer.done, stderrFailure, timeout]);
      if (this.failure) throw this.failure;
      if (!this.turn) throw new FrameworkError(ErrorCode.PROCESS_EXITED);
      return this.turn;
    } finally {
      clearTimeout(timer);
    }
  }
}
