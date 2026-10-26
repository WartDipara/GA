(() => {
  'use strict';
  const element = (id) => document.getElementById(id);
  let interval = Number(element('live-view').dataset.interval);
  let paused = false;
  let pending = false;
  let timer;
  let snapshot;
  let revision = -1;
  const initialSelection = new URLSearchParams(location.hash.slice(1));
  let selectedRun = initialSelection.get('run') || '';
  let selectedEvent;
  function rememberSelection() {
    const state = new URLSearchParams({
      run: selectedRun,
      event: String(selectedEvent?.seq || ''),
    });
    history.replaceState(null, '', `#${state}`);
  }

  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }

  function button(text, action, className) {
    const result = node('button', text, className);
    result.type = 'button';
    result.addEventListener('click', action);
    return result;
  }

  function duration(value) {
    if (value === null || value === undefined) return 'Unavailable';
    if (value < 1000) return `${value.toLocaleString()} ms`;
    return `${(value / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })} s`;
  }

  function runLabel(run) {
    const match = /^apk:(\d+):(.+)$/.exec(run.label);
    return match ? `APK ${Number(match[1]) + 1} / ${match[2]}` : run.label;
  }

  function category(event) {
    if (/failed|incomplete|blocked|error/.test(event.kind)) return 'issues';
    if (/^(ai\.|provider\.|gameplay\.decision)/.test(event.kind)) return 'ai';
    if (/^(device\.|action\.|gameplay\.)/.test(event.kind)) return 'device';
    if (event.kind.startsWith('call.')) return 'module';
    return 'workflow';
  }

  function operation(event) {
    if (event.kind.startsWith('call.') && event.span_id) {
      const start = snapshot.events.find(
        (entry) =>
          entry.kind === 'call.started' &&
          entry.run_id === event.run_id &&
          entry.span_id === event.span_id,
      );
      if (start) return start.label;
    }
    return event.label;
  }

  function metric(label, value, description, tone = '') {
    const card = node('article', undefined, `metric ${tone}`);
    card.append(
      node('span', label, 'metric-label'),
      node('strong', value),
      node('small', description),
    );
    element('live-metrics').append(card);
  }

  function chooseRun(runId) {
    selectedRun = runId;
    selectedEvent = undefined;
    rememberSelection();
    renderNavigation();
    renderEvents();
  }

  function renderNavigation() {
    const scrollPosition = element('execution-tree').scrollTop;
    const entries = snapshot.executions.map((run) => {
      const entry = button(undefined, () => chooseRun(run.run_id), 'execution-item');
      entry.classList.toggle('active', selectedRun === run.run_id);
      entry.classList.toggle('child-execution', Boolean(run.parent_run));
      entry.title = run.run_id;
      const indicator = node('span', undefined, `status-dot ${run.status}`);
      const label = node('span', undefined, 'execution-label');
      label.append(node('strong', runLabel(run)), node('small', run.apk_name || run.stage));
      const publication = run.report_publication?.status;
      if (publication && publication !== 'in_progress')
        label.append(node('small', `Reports: ${publication}`));
      entry.append(indicator, label);
      entry.setAttribute('aria-pressed', String(selectedRun === run.run_id));
      return entry;
    });
    element('execution-tree').replaceChildren(...entries);
    element('execution-tree').scrollTop = scrollPosition;
    element('execution-count').textContent = snapshot.executions.length;
    element('all-executions').classList.toggle('active', !selectedRun);
    element('all-executions').setAttribute('aria-pressed', String(!selectedRun));
  }

  function selectEvent(event) {
    selectedEvent = event;
    rememberSelection();
    renderEvents();
  }

  function renderCharts() {
    const counts = new Map();
    snapshot.executions.forEach((run) => counts.set(run.status, (counts.get(run.status) || 0) + 1));
    const breakdown = element('status-breakdown');
    breakdown.replaceChildren();
    const stack = node('div', undefined, 'status-stack');
    stack.setAttribute('role', 'img');
    stack.setAttribute(
      'aria-label',
      [...counts].map(([status, count]) => `${status}: ${count}`).join(', '),
    );
    for (const [status, count] of counts) {
      const segment = node('span', undefined, `status-segment ${status}`);
      segment.style.flex = count;
      segment.title = `${status}: ${count} executions`;
      stack.append(segment);
    }
    breakdown.append(stack);
    const legend = node('div', undefined, 'status-legend');
    for (const [status, count] of counts) {
      const item = node('span');
      item.append(
        node('i', undefined, `status-dot ${status}`),
        node('span', status),
        node('strong', count),
      );
      legend.append(item);
    }
    breakdown.append(legend);
    if (!counts.size) breakdown.append(node('p', 'No executions recorded.', 'empty-state'));

    const calls = snapshot.events
      .filter(
        (event) =>
          /^call\.(completed|failed|incomplete)$/.test(event.kind) &&
          Number.isFinite(event.duration_ms),
      )
      .sort((left, right) => right.duration_ms - left.duration_ms)
      .slice(0, 5);
    const chart = element('duration-chart');
    chart.replaceChildren();
    const maximum = Math.max(1, ...calls.map((event) => event.duration_ms));
    for (const event of calls) {
      const bar = button(
        undefined,
        () => {
          selectedRun = event.run_id;
          element('live-search').value = '';
          element('event-category').value = 'all';
          selectedEvent = event;
          renderNavigation();
          renderEvents();
        },
        'duration-row',
      );
      const label = node('span', operation(event), 'bar-label');
      const track = node('span', undefined, 'bar-track');
      const fill = node('span', undefined, `bar-fill ${category(event)}`);
      fill.style.width = `${(100 * event.duration_ms) / maximum}%`;
      track.append(fill);
      bar.title = `${operation(event)}: ${duration(event.duration_ms)}. Open event details.`;
      bar.append(label, track, node('strong', duration(event.duration_ms)));
      chart.append(bar);
    }
    if (!calls.length)
      chart.append(node('p', 'No completed call timings in this window.', 'empty-state'));
  }

  function renderInspector() {
    const inspector = element('event-inspector');
    inspector.replaceChildren();
    if (!selectedEvent) {
      element('inspector-sequence').textContent = 'SELECT EVENT';
      inspector.append(node('p', 'No events match the current view.', 'empty-state'));
      return;
    }
    const event = selectedEvent;
    element('inspector-sequence').textContent = `EVENT #${event.seq}`;
    inspector.append(
      node('span', category(event), `event-type ${category(event)}`),
      node('h3', operation(event), 'inspector-title'),
      node('p', event.kind, 'subtle'),
    );
    const metadata = node('dl', undefined, 'metadata');
    const run = snapshot.executions.find((entry) => entry.run_id === event.run_id);
    for (const [label, value] of [
      ['Execution', run ? runLabel(run) : event.run_id],
      ['Duration', duration(event.duration_ms)],
      ['Run ID', event.run_id],
      ['Span ID', event.span_id || 'Unavailable'],
      ['Parent span', event.parent_span_id || 'Unavailable'],
    ])
      metadata.append(node('dt', label), node('dd', value));
    inspector.append(metadata);
    if (run) {
      const link = node('a', 'Open full execution trace', 'trace-link');
      const source = encodeURIComponent(`${location.pathname}${location.hash}`);
      link.href = `${run.trace_url}?from=${source}#view=calls&span=${encodeURIComponent(event.span_id || event.run_id)}`;
      inspector.append(link);
      const overview = node('a', 'Execution overview', 'trace-link');
      overview.href = `${run.index_url}?from=${source}`;
      inspector.append(overview);
      const catalog = node('a', 'Open exploration catalog', 'trace-link');
      catalog.href = `${run.exploration_url}?from=${source}`;
      inspector.append(catalog);
    }
    if (!snapshot.events.some((entry) => entry.seq === event.seq)) {
      inspector.append(node('p', 'Pinned event is outside the latest 200-event window.', 'subtle'));
    }
    inspector.append(node('h4', 'Recorded payload'), node('pre', event.detail));
    if (event.truncated)
      inspector.append(node('p', 'Preview truncated. Open the full execution trace.', 'subtle'));
  }

  function renderEvents() {
    if (!snapshot) return;
    const scrollPosition = element('live-events').scrollTop;
    const query = element('live-search').value.toLowerCase();
    const kind = element('event-category').value;
    const visible = snapshot.events.filter(
      (event) =>
        (!selectedRun || event.run_id === selectedRun) &&
        (kind === 'all' || category(event) === kind) &&
        `${event.kind} ${event.label} ${event.detail}`.toLowerCase().includes(query),
    );
    if (!selectedEvent) selectedEvent = visible[0];
    const rows = visible.map((event) => {
      const row = button(undefined, () => selectEvent(event), 'event-row');
      row.classList.toggle('selected', selectedEvent?.seq === event.seq);
      row.setAttribute('aria-pressed', String(selectedEvent?.seq === event.seq));
      const marker = node(
        'span',
        category(event).slice(0, 1).toUpperCase(),
        `event-marker ${category(event)}`,
      );
      const text = node('span', undefined, 'event-label');
      text.append(node('strong', operation(event)), node('small', `#${event.seq} / ${event.kind}`));
      row.append(marker, text, node('span', duration(event.duration_ms), 'event-duration'));
      return row;
    });
    if (!rows.length) rows.push(node('p', 'No events match the current filters.', 'empty-state'));
    element('live-events').replaceChildren(...rows);
    element('live-events').scrollTop = scrollPosition;
    element('event-count').textContent = visible.length;
    const run = snapshot.executions.find((entry) => entry.run_id === selectedRun);
    element('window-note').textContent =
      `${run ? runLabel(run) : 'All executions'} / Newest first / Bounded event window`;
    renderInspector();
  }

  function render() {
    if (!selectedEvent)
      selectedEvent = snapshot.events.find(
        (event) => String(event.seq) === initialSelection.get('event'),
      );
    element('task-title').textContent = snapshot.title;
    element('task-reference').textContent = snapshot.task_id;
    element('live-metrics').replaceChildren();
    const count = (status) => snapshot.executions.filter((run) => run.status === status).length;
    metric('Executions', snapshot.executions.length, 'Stages in this task');
    metric('Running', count('running'), 'Currently active executions', 'accent');
    metric('Succeeded', count('succeeded'), 'Completed executions', 'positive');
    metric(
      'Needs attention',
      count('blocked') + count('failed'),
      'Blocked or failed executions',
      'warning',
    );
    if (selectedEvent)
      selectedEvent =
        snapshot.events.find((event) => event.seq === selectedEvent.seq) || selectedEvent;
    renderNavigation();
    renderCharts();
    renderEvents();
  }

  function schedule() {
    clearTimeout(timer);
    if (!paused) timer = setTimeout(refresh, interval * 1000);
  }

  async function refresh() {
    if (pending) return;
    clearTimeout(timer);
    pending = true;
    try {
      const response = await fetch(`snapshot?after=${revision}`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (response.status !== 204) {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        snapshot = await response.json();
        revision = snapshot.revision;
        render();
      }
      element('refresh-error').hidden = true;
      element('live-indicator').classList.remove('disconnected');
      element('refresh-state').textContent = `Updated ${new Date().toLocaleTimeString()}`;
      element('live-indicator').textContent = paused ? 'Paused' : `Polling / ${interval}s`;
    } catch (error) {
      element('refresh-error').hidden = false;
      element('refresh-error').textContent =
        `Refresh failed (${error.message}). The previous snapshot is retained. Polling will retry.`;
      element('live-indicator').textContent = 'Disconnected';
      element('live-indicator').classList.add('disconnected');
    } finally {
      pending = false;
      schedule();
    }
  }

  const intervals = [...new Set([1, 3, 5, 10, 30, interval])].sort((left, right) => left - right);
  element('refresh-interval').replaceChildren(
    ...intervals.map((value) => new Option(`${value}s`, value)),
  );
  element('refresh-interval').value = interval;
  element('refresh-interval').addEventListener('change', (event) => {
    interval = Number(event.target.value);
    element('live-indicator').textContent = paused ? 'Paused' : `Polling / ${interval}s`;
    schedule();
  });
  element('pause-refresh').addEventListener('click', () => {
    paused = !paused;
    element('live-indicator').classList.toggle('paused', paused);
    element('pause-refresh').textContent = paused ? 'Resume' : 'Pause';
    element('live-indicator').textContent = paused ? 'Paused' : `Polling / ${interval}s`;
    if (paused) clearTimeout(timer);
    else refresh();
  });
  element('theme-toggle').addEventListener('click', () => {
    const light = getComputedStyle(document.documentElement).colorScheme !== 'light';
    document.documentElement.dataset.theme = light ? 'light' : 'dark';
    try {
      localStorage.setItem('game-auto-theme', light ? 'light' : 'dark');
    } catch {
      // Theme selection remains available when browser storage is disabled.
    }
    element('theme-toggle').textContent = light ? 'Dark' : 'Light';
  });
  element('all-executions').addEventListener('click', () => chooseRun(''));
  element('refresh-now').addEventListener('click', refresh);
  for (const [id, eventName] of [
    ['live-search', 'input'],
    ['event-category', 'change'],
  ]) {
    element(id).addEventListener(eventName, () => {
      selectedEvent = undefined;
      renderEvents();
    });
  }
  refresh();
})();
