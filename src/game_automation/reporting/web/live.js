(() => {
  'use strict';
  const element = (id) => document.getElementById(id);
  let interval = Number(element('live-view').dataset.interval);
  let paused = false;
  let pending = false;
  let timer;
  let snapshot;
  let closed = false;
  let metadataKey = '';
  const eventRows = new Map();
  let inspectorSequence;
  const localTime = (value) => {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime())
      ? date.toLocaleString(undefined, {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false, fractionalSecondDigits: 3,
      })
      : 'Unknown time';
  };
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
    const report = element('selected-report');
    const selected = snapshot.executions.find((run) => run.run_id === selectedRun);
    report.hidden = !selected || ['running', 'queued'].includes(selected.status);
    if (!report.hidden) report.href = selected.index_url;
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
    const events = snapshot.events.filter((event) => Number.isFinite(Date.parse(event.timestamp)));
    const chart = element('activity-chart');
    if (!events.length) return;
    const timestamps = events.map((event) => Date.parse(event.timestamp));
    const latest = Math.max(...timestamps);
    const earliest = Math.max(Math.min(...timestamps), latest - 119 * 10000);
    const start = Math.floor(earliest / 10000) * 10000;
    const end = Math.floor(latest / 10000) * 10000;
    const buckets = Array.from({ length: Math.floor((end - start) / 10000) + 1 },
      () => [0, 0, 0]);
    for (const event of events) {
      const index = Math.floor((Date.parse(event.timestamp) - start) / 10000);
      const bucket = buckets[index];
      if (!bucket) continue;
      if (event.kind === 'call.started') bucket[0] += 1;
      if (event.kind === 'call.failed') bucket[1] += 1;
      if (event.kind === 'call.started' && event.label === 'ai.decide') bucket[2] += 1;
    }
    const maximum = Math.max(1, ...buckets.flat());
    for (let series = 0; series < 3; series += 1) {
      const points = buckets.map((bucket, index) =>
        `${20 + index / Math.max(1, buckets.length - 1) * 960},${170 - bucket[series] / maximum * 150}`,
      ).join(' ');
      element(`activity-series-${series}`).setAttribute('points', points);
    }
    chart.setAttribute('aria-label', `Recorded events per 10 seconds; maximum ${maximum}`);
    element('activity-scale').textContent = `0–${maximum} events / 10s`;
    element('activity-start').textContent = localTime(start);
    element('activity-end').textContent = localTime(end);
  }

  function renderInspector() {
    if (inspectorSequence === selectedEvent?.seq) return;
    inspectorSequence = selectedEvent?.seq;
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
      ['Recorded at (local)', localTime(event.timestamp)],
      ['Event sequence', `#${event.seq}`],
      ['Execution', run ? runLabel(run) : event.run_id],
      ['Duration', duration(event.duration_ms)],
      ['Run ID', event.run_id],
      ['Span ID', event.span_id || 'Unavailable'],
      ['Parent span', event.parent_span_id || 'Unavailable'],
    ])
      metadata.append(node('dt', label), node('dd', value));
    inspector.append(metadata);
    if (run && !['running', 'queued'].includes(run.status)) {
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
    const eventList = element('live-events');
    const scrollPosition = eventList.scrollTop;
    const following = eventList.scrollHeight - scrollPosition - eventList.clientHeight < 40;
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
      const existing = eventRows.get(event.seq);
      if (existing) {
        existing.classList.toggle('selected', selectedEvent?.seq === event.seq);
        existing.setAttribute('aria-pressed', String(selectedEvent?.seq === event.seq));
        return existing;
      }
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
      const time = node('time', localTime(event.timestamp), 'event-timestamp');
      if (event.timestamp) time.dateTime = event.timestamp;
      row.append(time, marker, text, node('span', duration(event.duration_ms), 'event-duration'));
      eventRows.set(event.seq, row);
      return row;
    });
    if (!rows.length) rows.push(node('p', 'No events match the current filters.', 'empty-state'));
    const list = element('live-events');
    const retained = new Set(rows);
    for (const child of [...list.children]) if (!retained.has(child)) child.remove();
    rows.forEach((row, index) => {
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
    const sequences = new Set(snapshot.events.map((event) => event.seq));
    for (const sequence of eventRows.keys()) {
      if (!sequences.has(sequence)) eventRows.delete(sequence);
    }
    element('live-events').scrollTop = following ? eventList.scrollHeight : scrollPosition;
    element('event-count').textContent = visible.length;
    const run = snapshot.executions.find((entry) => entry.run_id === selectedRun);
    element('window-note').textContent =
      `${run ? runLabel(run) : 'All executions'} / Oldest → newest by event sequence / Local time`;
    renderInspector();
  }

  function render() {
    if (!selectedEvent)
      selectedEvent = snapshot.events.find(
        (event) => String(event.seq) === initialSelection.get('event'),
      );
    element('task-title').textContent = snapshot.title;
    element('task-reference').textContent = snapshot.task_id;
    const nextMetadata = JSON.stringify(snapshot.executions);
    if (metadataKey !== nextMetadata) {
      metadataKey = nextMetadata;
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
      renderNavigation();
      inspectorSequence = undefined;
    }
    if (selectedEvent)
      selectedEvent =
        snapshot.events.find((event) => event.seq === selectedEvent.seq) || selectedEvent;
    renderCharts();
    renderEvents();
  }

  function schedule() {
    clearTimeout(timer);
    if (!paused && !closed && !document.hidden)
      timer = setTimeout(refresh, snapshot?.has_more ? 250 : interval * 1000);
  }

  async function refresh() {
    if (pending || closed || document.hidden) return;
    clearTimeout(timer);
    pending = true;
    try {
      const response = await fetch(`snapshot?after=${revision}`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (response.status !== 204) {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = await response.json();
        const changed = next.events.length || JSON.stringify(next.executions) !== metadataKey;
        const events = new Map((snapshot?.events || []).map((event) => [event.seq, event]));
        next.events.forEach((event) => events.set(event.seq, event));
        next.events = [...events.values()].sort((left, right) => left.seq - right.seq).slice(-200);
        snapshot = next;
        revision = next.revision;
        if (changed) render();
      }
      element('refresh-error').hidden = true;
      element('live-indicator').classList.remove('disconnected');
      element('refresh-state').textContent = `Updated ${new Date().toLocaleTimeString()}`;
      const settled = snapshot && !snapshot.has_more && snapshot.executions.length &&
        snapshot.executions.every((run) => !['running', 'queued'].includes(run.status) &&
          run.report_publication?.status !== 'pending');
      if (settled) {
        paused = true;
        element('pause-refresh').textContent = 'Resume';
      }
      element('live-indicator').textContent = settled ? 'Settled / reports available' :
        paused ? 'Paused' : `Polling / ${interval}s`;
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
  addEventListener('pagehide', () => { closed = true; clearTimeout(timer); });
  addEventListener('pageshow', () => { closed = false; schedule(); });
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    if (!document.hidden && !paused) refresh();
  });
  refresh();
})();
