/** Keeps the working graph near the current page without holding the complete directory. */
export class NeighborhoodCache<T> {
  private readonly entries = new Map<string, { value: T[]; bytes: number }>();
  private bytes = 0;

  constructor(private readonly maximumEntries: number, private readonly maximumBytes: number) {}

  // Return a copy so callers can edit a record before committing without changing the cached snapshot.
  read(key: string, load: () => T[]): T[] {
    const existing = this.entries.get(key);
    if (existing) {
      this.entries.delete(key);
      this.entries.set(key, existing);
      return structuredClone(existing.value);
    }
    const value = load();
    const bytes = Buffer.byteLength(JSON.stringify(value));
    if (bytes <= this.maximumBytes) {
      while (this.entries.size >= this.maximumEntries || this.bytes + bytes > this.maximumBytes) {
        const oldest = this.entries.keys().next().value;
        if (oldest === undefined) break;
        this.invalidate(oldest);
      }
      this.entries.set(key, { value: structuredClone(value), bytes });
      this.bytes += bytes;
    }
    return value;
  }

  invalidate(key: string): void {
    const item = this.entries.get(key);
    if (!item) return;
    this.entries.delete(key);
    this.bytes -= item.bytes;
  }
}
