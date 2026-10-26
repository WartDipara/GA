import { performance } from 'node:perf_hooks';
import { ErrorCode, FrameworkError, frameworkError } from '@game-automation/contracts/errors';
import { decode, type ContractMap } from '@game-automation/contracts';

type Entry = ContractMap['plugin-event-batch'][number];
type Batch = Entry[];
const MAX_EVENTS = 16;
const MAX_BYTES = 256 * 1024;
const MAX_EVENT_BYTES = 4 * 1024 * 1024;
const FLUSH_INTERVAL_MS = 50;

function coalesce(entries: Batch): Batch {
  const result: Batch = [];
  let active: { key: string; entry: Entry; sources: string[]; fragments: number } | undefined;
  for (const entry of entries) {
    const text = entry.payload['text'];
    const source = entry.payload['source_event_id'];
    if (
      !['ai.progress', 'ai.reasoning_summary'].includes(entry.kind) ||
      typeof text !== 'string' ||
      typeof source !== 'string'
    ) {
      result.push(entry);
      if (entry.kind !== 'provider.wire') active = undefined;
      continue;
    }
    const key = JSON.stringify([
      entry.kind,
      entry.payload['thread_id'],
      entry.payload['turn_id'],
      entry.payload['item_id'],
    ]);
    if (active?.key === key) {
      active.sources.push(source);
      active.fragments++;
      active.entry.payload['text'] = String(active.entry.payload['text']) + text;
      active.entry.payload['fragment_count'] = active.fragments;
    } else {
      const sources = [source];
      const payload: Record<string, unknown> = {
        ...entry.payload,
        source_event_ids: sources,
        fragment_count: 1,
      };
      delete payload.source_event_id;
      const merged = { ...entry, payload };
      active = { key, entry: merged, sources, fragments: 1 };
      result.push(merged);
    }
  }
  return result;
}

export class TraceBuffer {
  private entries: Batch = [];
  private bytes = 0;
  private writing: Promise<void> = Promise.resolve();
  private timer: NodeJS.Timeout | undefined;
  private failure: FrameworkError | undefined;
  private batches = 0;
  private events = 0;
  private receivedEvents = 0;
  private encodedBytes = 0;
  private peakBytes = 0;
  private peakEvents = 0;
  private persistMs = 0;

  constructor(
    private readonly persist: (batch: ContractMap['plugin-event-batch']) => Promise<void>,
    private readonly abort: (error: FrameworkError) => void,
  ) {}

  async append(entries: Batch, durable: boolean): Promise<void> {
    await this.writing;
    if (this.failure) throw this.failure;
    for (const entry of entries) {
      const bytes = Buffer.byteLength(JSON.stringify(entry));
      if (bytes > MAX_EVENT_BYTES) throw new FrameworkError(ErrorCode.MESSAGE_TOO_LARGE);
      if (
        this.entries.length &&
        (this.bytes + bytes > MAX_BYTES || this.entries.length >= MAX_EVENTS)
      )
        await this.flush();
      this.entries.push(entry);
      this.bytes += bytes;
      this.peakBytes = Math.max(this.peakBytes, this.bytes);
      this.peakEvents = Math.max(this.peakEvents, this.entries.length);
      if (this.bytes >= MAX_BYTES || this.entries.length >= MAX_EVENTS) await this.flush();
    }
    if (durable) await this.flush();
    else if (!this.timer && this.entries.length)
      this.timer = setTimeout(() => {
        void this.flush().catch((error: unknown) => this.fail(error));
      }, FLUSH_INTERVAL_MS);
  }

  async flush(): Promise<void> {
    clearTimeout(this.timer);
    this.timer = undefined;
    if (this.failure) throw this.failure;
    if (!this.entries.length) return this.writing;
    const entries = this.entries;
    const bytes = this.bytes;
    this.entries = [];
    this.bytes = 0;
    const write = this.writing.then(async () => {
      if (this.failure) throw this.failure;
      const started = performance.now();
      const batch = decode('plugin-event-batch', coalesce(entries));
      await this.persist(batch);
      this.persistMs += performance.now() - started;
      this.batches++;
      this.events += batch.length;
      this.receivedEvents += entries.length;
      this.encodedBytes += bytes;
    });
    this.writing = write;
    void write.catch((error: unknown) => this.fail(error));
    await write;
  }

  stats(): Record<string, unknown> {
    const result = {
      batches: this.batches,
      events: this.events,
      received_events: this.receivedEvents,
      received_encoded_bytes: this.encodedBytes,
      peak_buffer_bytes: this.peakBytes,
      peak_buffer_events: this.peakEvents,
      flush_duration_ms: this.persistMs,
      flush_interval_ms: FLUSH_INTERVAL_MS,
      max_batch_bytes: MAX_BYTES,
      max_single_event_bytes: MAX_EVENT_BYTES,
      max_batch_events: MAX_EVENTS,
      scope: 'provider_trace_buffer',
    };
    return result;
  }

  private fail(error: unknown): void {
    this.failure ??= frameworkError(error);
    clearTimeout(this.timer);
    this.timer = undefined;
    this.abort(this.failure);
  }
}
