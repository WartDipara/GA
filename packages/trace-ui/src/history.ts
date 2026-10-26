import { historySearch, historyView, type HistoryView } from '@game-automation/contracts/workspace';
import {
  HistoryIndex,
  pageSize,
  type SearchIndex,
  type SearchQuery,
  type SearchResult,
} from './history-index.js';

function element(id: string): HTMLElement {
  const value = document.getElementById(`history-${id}`);
  if (!value) throw new Error(`Missing history control: ${id}`);
  return value;
}

function historyControls() {
  const search = element('search');
  const state = element('state');
  const previous = element('previous');
  const next = element('next');
  if (
    !(search instanceof HTMLInputElement) ||
    !(state instanceof HTMLSelectElement) ||
    !(previous instanceof HTMLButtonElement) ||
    !(next instanceof HTMLButtonElement)
  )
    return null;
  const controls = { search, state, previous, next, rows: element('rows') };
  return controls;
}

export function mountHistory(): void {
  const source = document.getElementById('history-data');
  if (!source) return;
  let value: unknown;
  try {
    value = JSON.parse(source.textContent ?? 'null');
  } catch {
    element('count').textContent = 'Task records unavailable';
    return;
  }
  const initial = historyView(value);
  if (!initial) {
    element('count').textContent = 'Task records unavailable';
    return;
  }
  let data: HistoryView = initial;
  let signature = JSON.stringify(data.tasks);
  const controls = historyControls();
  if (!controls) return;
  const { search, state, previous, next, rows } = controls;

  const saved = new URLSearchParams(location.hash.slice(1));
  search.value = (saved.get('q') ?? '').slice(0, 256);
  const savedState = saved.get('state');
  state.value = savedState === 'running' || savedState === 'issues' ? savedState : 'all';
  const savedOffset = Number(saved.get('offset') ?? 0);
  let offset =
    Number.isSafeInteger(savedOffset) && savedOffset >= 0
      ? Math.floor(savedOffset / pageSize) * pageSize
      : 0;
  let revision = 0;
  let serial = 0;
  let worker: Worker | null = null;
  const fallback = new HistoryIndex();
  let running: SearchQuery | null = null;
  let pending: SearchQuery | null = null;
  let frame = 0;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let pollController: AbortController | null = null;
  let active = true;
  let composing = false;
  let lifecycle = 0;
  let historyTag: string | null = null;

  function index(): void {
    clearTimeout(deadline);
    running = null;
    pending = null;
    const message: SearchIndex = {
      type: 'index',
      revision,
      items: data.tasks.map((task) => {
        const item = {
          text: `${task.title} ${task.package_name ?? ''} ${task.task_id}`,
          running: task.running,
          issues: task.issues,
        };
        return item;
      }),
    };
    if (worker) worker.postMessage(message);
    else fallback.replace(message);
  }

  function remember(): void {
    const selection = new URLSearchParams({
      q: search.value,
      state: state.value,
      offset: String(offset),
    });
    history.replaceState(null, '', `#${selection}`);
  }

  function render(result: SearchResult): void {
    offset = result.offset;
    const fragment = document.createDocumentFragment();
    for (const index of result.indices) {
      const task = data.tasks[index];
      if (!task) continue;
      const row = document.createElement('tr');
      const label = document.createElement('td');
      const link = document.createElement('a');
      link.href = task.href;
      link.textContent = task.title;
      const reference = document.createElement('small');
      reference.textContent = task.task_id;
      label.append(link, reference);
      row.append(label);
      for (const value of [
        task.executions,
        task.running,
        task.issues,
        task.updated_at ? new Date(task.updated_at).toLocaleString() : 'No execution yet',
      ]) {
        const cell = document.createElement('td');
        cell.textContent = String(value);
        row.append(cell);
      }
      fragment.append(row);
    }
    if (!result.total) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 5;
      cell.className = 'history-empty';
      cell.textContent = 'No games or tasks match this search.';
      row.append(cell);
      fragment.append(row);
    }
    rows.replaceChildren(fragment);
    rows.setAttribute('aria-busy', 'false');
    rows.inert = false;
    const start = result.total ? offset + 1 : 0;
    element('count').textContent =
      `${result.total} matches · ${start}–${offset + result.indices.length} of ${data.tasks.length} tasks`;
    previous.disabled = offset === 0;
    next.disabled = offset + pageSize >= result.total;
    remember();
  }

  function accept(result: SearchResult): void {
    if (!active || result.request_id !== running?.request_id) return;
    clearTimeout(deadline);
    running = null;
    if (result.request_id === serial && result.revision === revision) render(result);
    pump();
  }

  function workerFailed(): void {
    if (!active) return;
    worker?.terminate();
    worker = null;
    index();
    schedule();
  }

  function startWorker(): void {
    const bundle = document.querySelector('script[src$="/trace_ui.js"]');
    try {
      if (location.protocol === 'file:' || !(bundle instanceof HTMLScriptElement)) return;
      const candidate = new Worker(new URL('history_search.js', bundle.src));
      worker = candidate;
      candidate.addEventListener('message', (event: MessageEvent<unknown>) => {
        if (worker !== candidate) return;
        const result = historySearch(event.data);
        if (result?.type !== 'result') {
          workerFailed();
          return;
        }
        if (result.request_id !== running?.request_id) return;
        if (
          result.revision === revision &&
          (result.total > data.tasks.length ||
            result.indices.some((index) => index >= data.tasks.length))
        ) {
          workerFailed();
          return;
        }
        accept(result);
      });
      const failed = () => {
        if (worker === candidate) workerFailed();
      };
      candidate.addEventListener('error', failed);
      candidate.addEventListener('messageerror', failed);
    } catch {
      worker = null;
    }
  }

  function pump(): void {
    if (!active || running || !pending) return;
    const query = pending;
    pending = null;
    running = query;
    if (worker) {
      deadline = setTimeout(workerFailed, 5000);
      worker.postMessage(query);
    } else {
      void fallback
        .search(query, () => !active || query.request_id !== serial)
        .then((result) => {
          if (result) accept(result);
          else if (running?.request_id === query.request_id) {
            running = null;
            pump();
          }
        })
        .catch(() => {
          if (running?.request_id !== query.request_id) return;
          running = null;
          rows.setAttribute('aria-busy', 'false');
          rows.inert = false;
          element('count').textContent = 'Search unavailable; retained results';
        });
    }
  }

  function schedule(): void {
    serial += 1;
    cancelAnimationFrame(frame);
    rows.setAttribute('aria-busy', 'true');
    rows.inert = true;
    element('count').textContent = 'Searching…';
    previous.disabled = true;
    next.disabled = true;
    frame = requestAnimationFrame(() => {
      const selected = state.value;
      pending = {
        type: 'query',
        request_id: serial,
        revision,
        query: search.value,
        state: selected === 'running' || selected === 'issues' ? selected : 'all',
        offset,
      };
      pump();
    });
  }

  function changed(): void {
    if (composing) return;
    offset = 0;
    remember();
    schedule();
  }

  search.addEventListener('compositionstart', () => {
    composing = true;
  });
  search.addEventListener('compositionend', () => {
    composing = false;
    changed();
  });
  search.addEventListener('input', changed);
  state.addEventListener('change', changed);
  previous.addEventListener('click', () => {
    offset = Math.max(0, offset - pageSize);
    schedule();
  });
  next.addEventListener('click', () => {
    offset += pageSize;
    schedule();
  });

  async function refresh(): Promise<void> {
    if (!active || location.protocol === 'file:') return;
    const generation = lifecycle;
    pollController = new AbortController();
    try {
      const signal = AbortSignal.any([pollController.signal, AbortSignal.timeout(10000)]);
      const headers: Record<string, string> = {};
      if (historyTag) headers['If-None-Match'] = historyTag;
      const response = await fetch('history.json', { signal, headers, cache: 'no-store' });
      if (response.status === 304) {
        if (active && lifecycle === generation)
          element('connection').textContent = 'Read-only polling / 3s';
        return;
      }
      if (!response.ok) throw new Error('History unavailable');
      const value: unknown = await response.json();
      const updated = historyView(value);
      if (!updated) throw new Error('Invalid history');
      if (!active || lifecycle !== generation) return;
      historyTag = response.headers.get('ETag');
      const nextSignature = JSON.stringify(updated.tasks);
      if (signature !== nextSignature) {
        data = updated;
        signature = nextSignature;
        revision += 1;
        index();
        schedule();
      }
      element('connection').textContent = 'Read-only polling / 3s';
    } catch {
      if (active && lifecycle === generation)
        element('connection').textContent = 'Refresh unavailable; retained history';
    } finally {
      if (active && lifecycle === generation)
        pollTimer = setTimeout(() => {
          void refresh();
        }, 3000);
    }
  }

  startWorker();
  index();
  schedule();
  void refresh();
  addEventListener('pagehide', () => {
    active = false;
    lifecycle += 1;
    worker?.terminate();
    worker = null;
    pollController?.abort();
    clearTimeout(pollTimer);
    clearTimeout(deadline);
    cancelAnimationFrame(frame);
    pending = null;
    running = null;
  });
  addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    active = true;
    startWorker();
    index();
    schedule();
    void refresh();
  });
}
