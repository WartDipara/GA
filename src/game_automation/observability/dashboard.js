/* Independent offline UI. Data is projected by Python; this file never calls a service. */
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('insight-data').textContent);
  const $ = id => document.getElementById(id);
  const fmt = value => value === null || value === undefined ? 'Unavailable' : Number(value).toLocaleString('en-US');
  let picked = data.requests.length - 1;
  let category = 'all';
  const node = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const button = (text, click, cls = '') => {
    const element = node('button', text, cls);
    element.type = 'button';
    element.addEventListener('click', click);
    return element;
  };
  const reset = id => { $(id).replaceChildren(); return $(id); };
  const select = index => { picked = index; category = 'all'; render(); };
  const metric = (label, value, foot) => {
    const card = node('div', undefined, 'metric');
    card.append(node('span', label), node('strong', value), node('small', foot));
    $('metrics').append(card);
  };
  const summary = data.summary;
  metric('Module calls', fmt(summary.calls), `${summary.failed_calls} failed  |  ${summary.incomplete_calls} incomplete`);
  metric('Observed AI turns', fmt(summary.ai_turns), 'App Server / application boundary');
  metric('Reported tokens', fmt(summary.usage.totalTokens), `Attributed turns ${summary.usage_coverage}${summary.usage_complete ? '' : '  |  partial data'}`);
  metric('Run status', data.run.status, `${summary.events} durable events`);
  $('run-title').textContent = data.run.id;
  $('run-meta').textContent = `${data.run.mode}  |  ${data.run.stage}  |  ${data.run.created_at}`;
  $('visibility').textContent = 'Visibility: application submissions and public provider events. Full model context, internal request counts, and cost are unavailable. Approximate text estimates are not billing or window occupancy.';
  $('request-select').addEventListener('change', event => select(Number(event.target.value)));
  $('content-search').addEventListener('input', () => renderBrowser());
  $('event-filter').addEventListener('change', () => renderEvents());
  $('event-search').addEventListener('input', () => renderEvents());

  function render() {
    const request = data.requests[picked];
    reset('request-select');
    data.requests.forEach((entry, index) => {
      const option = node('option', `Turn ${index + 1}  |  ${entry.status}  |  ${entry.turn_id || entry.source}`);
      option.value = index;
      option.selected = picked === index;
      $('request-select').append(option);
    });
    reset('trend');
    const max = Math.max(1, ...data.requests.map(r => r.estimated_input_tokens));
    data.requests.forEach((entry, index) => {
      const bar = button('', () => select(index), `trend-step ${index === picked ? 'selected' : ''}`);
      bar.setAttribute('aria-label', `Select turn ${index + 1}`);
      bar.title = `Turn ${index + 1}: visible input ~${entry.estimated_input_tokens} tokens`;
      const fill = node('span', undefined, 'trend-fill');
      fill.style.height = `${Math.max(3, 100 * entry.estimated_input_tokens / max)}%`;
      bar.append(fill, node('small', String(index + 1)));
      $('trend').append(bar);
    });
    if (!request) {
      $('request-info').textContent = 'No AI turns were observed in this run. Module execution records remain available below.';
      reset('composition'); reset('legend'); reset('categories'); reset('browser'); reset('actuals');
      renderEvents();
      return;
    }
    $('request-info').textContent = `${request.source}  |  ${request.status}  |  ${request.timestamp}  |  span ${request.span_id || 'Unavailable'}`;
    reset('actuals');
    const usage = request.usage;
    for (const [label, key] of [['Input', 'inputTokens'], ['Output (including reasoning)', 'outputTokens'], ['Cached input', 'cachedInputTokens'], ['Reasoning output (subset)', 'reasoningOutputTokens']]) {
      $('actuals').append(node('div', `${label}: ${fmt(usage?.observed_turn_delta?.[key])}`));
    }
    $('actuals').append(node('small', usage?.run_attribution === 'known' ? 'Turn deltas use reported cumulative counters from the same thread. Repeated snapshots are not added.' : 'The baseline is unknown or usage is missing. Last-request usage cannot stand in for a complete turn.'));
    if (usage?.last_model_request) {
      const detail = node('details');
      detail.append(node('summary', 'Provider last model request / thread totals'),
        node('pre', JSON.stringify({last: usage.last_model_request, thread_total: usage.thread_total}, null, 2)));
      $('actuals').append(detail);
    }
    const total = Math.max(1, Object.values(request.composition).reduce((sum, c) => sum + c.estimated_tokens, 0));
    reset('composition'); reset('legend');
    for (const [key, label] of Object.entries(data.categories)) {
      const values = request.composition[key];
      if (!values.items) continue;
      const segment = button('', () => { category = key; renderBrowser(); }, `segment cat-${key}`);
      segment.style.width = `${100 * values.estimated_tokens / total}%`;
      segment.title = `${label}: ${values.items} items,~${fmt(values.estimated_tokens)} tokens`;
      segment.setAttribute('aria-label', segment.title);
      $('composition').append(segment);
      $('legend').append(button(`${label} ${values.items} items / ~${fmt(values.estimated_tokens)}`, () => { category = key; renderBrowser(); }, `legend-item cat-${key}`));
    }
    $('diff').textContent = request.diff.comparable ? `Compared with the previous turn in this thread: ${request.diff.added.length} added, ${request.diff.removed.length} removed (application submissions only; server-side pruning is not inferred).` : 'No previous turn in this thread is available for comparison.';
    $('summary-state').textContent = request.summary_available ? 'Public reasoning summary: recorded' : 'Public reasoning summary: unavailable; see assistant output or execution events for decision notes';
    reset('notes');
    request.notes.forEach(text => $('notes').append(node('p', text)));
    renderBrowser(); renderEvents();
  }

  function renderBrowser() {
    const request = data.requests[picked];
    reset('categories'); reset('browser');
    if (!request) return;
    for (const [key, label] of [['all', 'All'], ...Object.entries(data.categories)]) {
      const count = request.items.filter(i => key === 'all' || i.category === key).length;
      $('categories').append(button(`${label} ${count}`, () => { category = key; renderBrowser(); }, category === key ? 'active' : ''));
    }
    const query = $('content-search').value.toLowerCase();
    const entries = request.items.filter(i => (category === 'all' || i.category === category) && `${i.title}\n${i.content}`.toLowerCase().includes(query));
    for (const entry of entries) {
      const details = node('details', undefined, 'context-item');
      details.dataset.category = entry.category;
      details.append(node('summary', `${data.categories[entry.category]} / ${entry.title}  |  ~${fmt(entry.estimated_tokens)} tokens`));
      if (request.diff.added.includes(entry.id)) details.append(node('small', 'Added or changed in this turn', 'delta-badge'));
      details.append(node('small', `Source event #${entry.source_seq}  |  SHA256 ${entry.sha256}`), node('pre', entry.content));
      if (entry.artifact_ref) details.append(node('small', `Frozen file:${entry.artifact_ref}  |  ${entry.availability}`));
      $('browser').append(details);
    }
    if (!entries.length) $('browser').append(node('p', 'No observed content matches this filter.', 'empty'));
  }

  function renderEvents() {
    const request = data.requests[picked];
    const query = $('event-search').value.toLowerCase();
    const scope = $('event-filter').value;
    reset('context-events');
    for (const event of data.context_events) {
      if (scope === 'request' && event.request_id !== request?.id) continue;
      if (!`${event.kind} ${event.detail}`.toLowerCase().includes(query)) continue;
      const detail = node('details', undefined, 'event-row');
      detail.append(node('summary', `#${event.seq}  |  ${event.kind}`), node('pre', event.detail));
      $('context-events').append(detail);
    }
  }
  render();
})();
