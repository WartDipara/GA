import type { HistorySearch } from '@game-automation/contracts/workspace';

export type SearchIndex = Extract<HistorySearch, { type: 'index' }>;
export type SearchQuery = Extract<HistorySearch, { type: 'query' }>;
export type SearchResult = Extract<HistorySearch, { type: 'result' }>;
export const pageSize = 50;

function normalized(value: string): string {
  return value.normalize('NFKC').toLowerCase().trim().replace(/\s+/g, ' ');
}

export class HistoryIndex {
  private items: SearchIndex['items'] = [];
  private matches: number[] = [];
  private previous: SearchQuery | null = null;

  replace(index: SearchIndex): void {
    this.items = index.items.map((item) => {
      const entry = { ...item, text: normalized(item.text) };
      return entry;
    });
    this.previous = null;
    this.matches = [];
  }

  async search(query: SearchQuery, cancelled: () => boolean): Promise<SearchResult | null> {
    const text = normalized(query.query);
    const terms = text.split(' ').filter(Boolean);
    const reusable = this.previous?.state === query.state && text.startsWith(this.previous.query);
    if (!reusable || this.previous?.query !== text) {
      const matches: number[] = [];
      const length = reusable ? this.matches.length : this.items.length;
      for (let position = 0; position < length; position += 1) {
        if (position % 512 === 0) {
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
          if (cancelled()) return null;
        }
        const index = reusable ? this.matches[position] : position;
        const item = index === undefined ? undefined : this.items[index];
        if (!item || index === undefined) continue;
        const stateMatches = query.state === 'all' || item[query.state] > 0;
        if (stateMatches && terms.every((term) => item.text.includes(term))) matches.push(index);
      }
      this.matches = matches;
      this.previous = { ...query, query: text };
    }
    if (cancelled()) return null;
    const total = this.matches.length;
    const lastPage = Math.max(0, Math.floor((total - 1) / pageSize) * pageSize);
    const offset = Math.min(query.offset, lastPage);
    const result: SearchResult = {
      type: 'result',
      revision: query.revision,
      request_id: query.request_id,
      offset,
      total,
      indices: this.matches.slice(offset, offset + pageSize),
    };
    return result;
  }
}
