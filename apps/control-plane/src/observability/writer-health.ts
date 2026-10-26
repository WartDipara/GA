import { statSync } from 'node:fs';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { decode, type ContractMap } from '@game-automation/contracts';
import { uid } from '../storage/values.js';

type Health = ContractMap['writer-health'];
type Measurement = Health['metrics']['transaction'];

function measurement(): Measurement {
  const value = { operations: 0, failures: 0, total_ms: 0, max_ms: 0, bytes: null };
  return value;
}

function fileSize(path: string): number | null {
  try {
    return statSync(path).size;
  } catch {
    return null;
  }
}

export class WriterHealth {
  private readonly id = uid('collector');
  private readonly started = performance.now();
  private lastSnapshot = this.started;
  private readonly metrics: Health['metrics'] = {
    transaction: measurement(),
    commit: measurement(),
    event_encode: measurement(),
    artifact_write: measurement(),
  };

  record(
    name: keyof Health['metrics'],
    started: number,
    failed = false,
    bytes: number | null = null,
  ): void {
    const value = this.metrics[name];
    const elapsed = performance.now() - started;
    value.operations++;
    value.failures += Number(failed);
    value.total_ms += elapsed;
    value.max_ms = Math.max(value.max_ms, elapsed);
    if (bytes !== null) value.bytes = (value.bytes ?? 0) + bytes;
  }

  snapshot(directory: string, force: boolean): Health | null {
    const now = performance.now();
    if (!force && now - this.lastSnapshot < 5_000) return null;
    this.lastSnapshot = now;
    const snapshot = {
      collector_id: this.id,
      scope: 'cumulative_writer_process',
      elapsed_ms: now - this.started,
      metrics: structuredClone(this.metrics),
      database_bytes: fileSize(join(directory, 'state.sqlite')),
      wal_bytes: fileSize(join(directory, 'state.sqlite-wal')),
      rss_bytes: process.memoryUsage.rss(),
      event_loop_utilization: performance.eventLoopUtilization().utilization,
    };
    return decode('writer-health', snapshot);
  }
}
