(() => {
  'use strict';
  const element = (id) => document.getElementById(id);
  const viewer = element('live-view');
  const expanded = new Set();
  let interval = Number(viewer.dataset.interval);
  let paused = false;
  let pending = false;
  let timer;
  let snapshot;
  let revision = -1;

  function node(tag, text) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function metric(label, value) {
    const card = node('div');
    card.className = 'metric';
    card.append(node('span', label), node('strong', value));
    element('live-metrics').append(card);
  }

  function renderEvents() {
    if (!snapshot) return;
    const runId = element('run-filter').value;
    const query = element('live-search').value.toLowerCase();
    const rows = [];
    for (const event of snapshot.events) {
      if (runId && event.run_id !== runId) continue;
      if (!`${event.kind} ${event.detail}`.toLowerCase().includes(query)) continue;
      const duration = event.duration_ms === null ? 'Unavailable' : `${event.duration_ms} ms`;
      const detail = node('details');
      detail.className = 'event-row';
      detail.open = expanded.has(event.seq);
      detail.addEventListener('toggle', () => {
        if (detail.open) expanded.add(event.seq);
        else expanded.delete(event.seq);
      });
      detail.append(
        node('summary', `#${event.seq} | ${event.kind} | Duration: ${duration}`),
        node('small', event.run_id),
        node('pre', event.detail),
      );
      if (event.truncated) detail.append(node('small', 'Preview truncated; open the execution trace.'));
      rows.push(detail);
    }
    element('live-events').replaceChildren(...rows);
  }

  function render() {
    element('task-title').textContent = snapshot.task_id;
    element('live-metrics').replaceChildren();
    const active = snapshot.executions.filter((run) => run.status === 'running').length;
    const blocked = snapshot.executions.filter((run) => run.status === 'blocked').length;
    metric('Executions', snapshot.executions.length);
    metric('Running', active);
    metric('Blocked', blocked);
    metric('Latest event', snapshot.revision);
    const selected = element('run-filter').value;
    const options = [new Option('All executions', '')];
    const rows = [];
    for (const run of snapshot.executions) {
      options.push(new Option(`${run.label} | ${run.run_id}`, run.run_id));
      const row = node('tr');
      const reference = node('td');
      const link = node('a', run.run_id);
      link.href = run.trace_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      reference.append(link);
      row.append(node('td', run.label), node('td', run.status), node('td', run.stage), reference);
      rows.push(row);
    }
    element('executions').replaceChildren(...rows);
    element('run-filter').replaceChildren(...options);
    element('run-filter').value = selected || '';
    const retained = new Set(snapshot.events.map((event) => event.seq));
    for (const seq of expanded) if (!retained.has(seq)) expanded.delete(seq);
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
      const response = await fetch(`/snapshot?after=${revision}`, {
        cache: 'no-store', signal: AbortSignal.timeout(10000),
      });
      if (response.status !== 204) {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        snapshot = await response.json();
        revision = snapshot.revision;
        render();
      }
      element('refresh-error').hidden = true;
      element('refresh-state').textContent =
        `Last checked: ${new Date().toLocaleTimeString()} | ${paused ? 'Paused' : `${interval}s polling`}`;
    } catch (error) {
      element('refresh-error').hidden = false;
      element('refresh-error').textContent =
        `Refresh failed (${error.message}). Showing the last successful snapshot; polling will retry.`;
    } finally {
      pending = false;
      schedule();
    }
  }

  const intervals = [...new Set([1, 3, 5, 10, 30, interval])].sort((left, right) => left - right);
  element('refresh-interval').replaceChildren(...intervals.map((value) => new Option(`${value}s`, value)));
  element('refresh-interval').value = interval;
  element('refresh-interval').addEventListener('change', (event) => {
    interval = Number(event.target.value);
    schedule();
  });
  element('pause-refresh').addEventListener('click', () => {
    paused = !paused;
    element('pause-refresh').textContent = paused ? 'Resume' : 'Pause';
    element('refresh-state').textContent = paused ? 'Automatic refresh paused' : 'Polling resumed';
    if (paused) clearTimeout(timer);
    else refresh();
  });
  element('refresh-now').addEventListener('click', refresh);
  element('run-filter').addEventListener('change', renderEvents);
  element('live-search').addEventListener('input', renderEvents);
  refresh();
})();
