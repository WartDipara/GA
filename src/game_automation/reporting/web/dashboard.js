/* Independent offline UI. Data is projected by Python; this file never calls a service. */
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('insight-data').textContent);
  const getElement = (id) => document.getElementById(id);
  const formatNumber = (value) =>
    value === null || value === undefined ? 'Unavailable' : Number(value).toLocaleString('en-US');
  let selectedRequestIndex = data.requests.length - 1;
  let category = 'all';
  const createElement = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) {
      element.textContent = text;
    }
    if (className) {
      element.className = className;
    }
    return element;
  };
  const createButton = (text, click, className = '') => {
    const element = createElement('button', text, className);
    element.type = 'button';
    element.addEventListener('click', click);
    return element;
  };
  const reset = (id) => {
    const element = getElement(id);
    element.replaceChildren();
    return element;
  };
  const select = (index) => {
    selectedRequestIndex = index;
    category = 'all';
    render();
  };
  const metric = (label, value, foot) => {
    const card = createElement('div', undefined, 'metric');
    card.append(
      createElement('span', label),
      createElement('strong', value),
      createElement('small', foot),
    );
    getElement('metrics').append(card);
  };
  const summary = data.summary;
  metric(
    'Module calls',
    formatNumber(summary.calls),
    `${summary.failed_calls} failed  |  ${summary.incomplete_calls} incomplete`,
  );
  metric('Observed AI turns', formatNumber(summary.ai_turns), 'App Server / application boundary');
  metric(
    'Reported tokens',
    formatNumber(summary.usage.totalTokens),
    `Attributed turns ${summary.usage_coverage}` +
      `${summary.usage_complete ? '' : '  |  partial data'}`,
  );
  metric('Run status', data.run.status, `${summary.events} durable events`);
  getElement('run-title').textContent = data.run.id;
  getElement('run-meta').textContent =
    `${data.run.mode}  |  ${data.run.stage}  |  ${data.run.created_at}`;
  getElement('visibility').textContent =
    'Visibility: application submissions and public provider events. ' +
    'Full model context, internal request counts, and cost are unavailable. ' +
    'Approximate text estimates are not billing or window occupancy.';
  getElement('request-select').addEventListener('change', (event) =>
    select(Number(event.target.value)),
  );
  getElement('content-search').addEventListener('input', () => renderBrowser());
  getElement('event-filter').addEventListener('change', () => renderEvents());
  getElement('event-search').addEventListener('input', () => renderEvents());

  function render() {
    const request = data.requests[selectedRequestIndex];
    reset('request-select');
    data.requests.forEach((entry, index) => {
      const option = createElement(
        'option',
        `Turn ${index + 1}  |  ${entry.status}  |  ${entry.turn_id || entry.source}`,
      );
      option.value = index;
      option.selected = selectedRequestIndex === index;
      getElement('request-select').append(option);
    });
    reset('trend');
    const max = Math.max(1, ...data.requests.map((request) => request.estimated_input_tokens));
    data.requests.forEach((entry, index) => {
      const bar = createButton(
        '',
        () => select(index),
        `trend-step ${index === selectedRequestIndex ? 'selected' : ''}`,
      );
      bar.setAttribute('aria-label', `Select turn ${index + 1}`);
      bar.title = `Turn ${index + 1}: visible input ~${entry.estimated_input_tokens} tokens`;
      const fill = createElement('span', undefined, 'trend-fill');
      fill.style.height = `${Math.max(3, (100 * entry.estimated_input_tokens) / max)}%`;
      bar.append(fill, createElement('small', String(index + 1)));
      getElement('trend').append(bar);
    });
    if (!request) {
      getElement('request-info').textContent =
        'No AI turns were observed in this run. Module execution records remain available below.';
      reset('composition');
      reset('legend');
      reset('categories');
      reset('browser');
      reset('actuals');
      renderEvents();
      return;
    }
    const requestDetails = [
      request.source,
      request.status,
      request.timestamp,
      `span ${request.span_id || 'Unavailable'}`,
    ];
    getElement('request-info').textContent = requestDetails.join('  |  ');
    reset('actuals');
    const usage = request.usage;
    for (const [label, key] of [
      ['Input', 'inputTokens'],
      ['Output (including reasoning)', 'outputTokens'],
      ['Cached input', 'cachedInputTokens'],
      ['Reasoning output (subset)', 'reasoningOutputTokens'],
    ]) {
      getElement('actuals').append(
        createElement('div', `${label}: ${formatNumber(usage?.observed_turn_delta?.[key])}`),
      );
    }
    getElement('actuals').append(
      createElement(
        'small',
        usage?.run_attribution === 'known'
          ? 'Turn deltas use reported cumulative counters from the same thread. ' +
              'Repeated snapshots are not added.'
          : 'The baseline is unknown or usage is missing. ' +
              'Last-request usage cannot stand in for a complete turn.',
      ),
    );
    if (usage?.last_model_request) {
      const detail = createElement('details');
      detail.append(
        createElement('summary', 'Provider last model request / thread totals'),
        createElement(
          'pre',
          JSON.stringify(
            { last: usage.last_model_request, thread_total: usage.thread_total },
            null,
            2,
          ),
        ),
      );
      getElement('actuals').append(detail);
    }
    const total = Math.max(
      1,
      Object.values(request.composition).reduce((sum, bucket) => sum + bucket.estimated_tokens, 0),
    );
    reset('composition');
    reset('legend');
    for (const [key, label] of Object.entries(data.categories)) {
      const values = request.composition[key];
      if (!values.items) {
        continue;
      }
      const segment = createButton(
        '',
        () => {
          category = key;
          renderBrowser();
        },
        `segment cat-${key}`,
      );
      segment.style.width = `${(100 * values.estimated_tokens) / total}%`;
      segment.title =
        `${label}: ${values.items} items, ~` + `${formatNumber(values.estimated_tokens)} tokens`;
      segment.setAttribute('aria-label', segment.title);
      getElement('composition').append(segment);
      getElement('legend').append(
        createButton(
          `${label} ${values.items} items / ~${formatNumber(values.estimated_tokens)}`,
          () => {
            category = key;
            renderBrowser();
          },
          `legend-item cat-${key}`,
        ),
      );
    }
    getElement('diff').textContent = request.diff.comparable
      ? 'Compared with the previous turn in this thread: ' +
        `${request.diff.added.length} added, ${request.diff.removed.length} removed ` +
        '(application submissions only; server-side pruning is not inferred).'
      : 'No previous turn in this thread is available for comparison.';
    getElement('summary-state').textContent = request.summary_available
      ? 'Public reasoning summary: recorded'
      : 'Public reasoning summary: unavailable; ' +
        'see assistant output or execution events for decision notes';
    reset('notes');
    request.notes.forEach((text) => getElement('notes').append(createElement('p', text)));
    renderBrowser();
    renderEvents();
  }

  function renderBrowser() {
    const request = data.requests[selectedRequestIndex];
    reset('categories');
    reset('browser');
    if (!request) {
      return;
    }
    for (const [key, label] of [['all', 'All'], ...Object.entries(data.categories)]) {
      const count = request.items.filter((entry) => key === 'all' || entry.category === key).length;
      getElement('categories').append(
        createButton(
          `${label} ${count}`,
          () => {
            category = key;
            renderBrowser();
          },
          category === key ? 'active' : '',
        ),
      );
    }
    const query = getElement('content-search').value.toLowerCase();
    const entries = request.items.filter(
      (entry) =>
        (category === 'all' || entry.category === category) &&
        `${entry.title}\n${entry.content}`.toLowerCase().includes(query),
    );
    for (const entry of entries) {
      const details = createElement('details', undefined, 'context-item');
      details.dataset.category = entry.category;
      details.append(
        createElement(
          'summary',
          `${data.categories[entry.category]} / ${entry.title}  |  ~` +
            `${formatNumber(entry.estimated_tokens)} tokens`,
        ),
      );
      if (request.diff.added.includes(entry.id))
        details.append(createElement('small', 'Added or changed in this turn', 'delta-badge'));
      details.append(
        createElement('small', `Source event #${entry.source_seq}  |  SHA256 ${entry.sha256}`),
        createElement('pre', entry.content),
      );
      if (entry.artifact_ref)
        details.append(
          createElement('small', `Frozen file:${entry.artifact_ref}  |  ${entry.availability}`),
        );
      getElement('browser').append(details);
    }
    if (!entries.length)
      getElement('browser').append(
        createElement('p', 'No observed content matches this filter.', 'empty'),
      );
  }

  function renderEvents() {
    const request = data.requests[selectedRequestIndex];
    const query = getElement('event-search').value.toLowerCase();
    const scope = getElement('event-filter').value;
    reset('context-events');
    for (const event of data.context_events) {
      if (scope === 'request' && event.request_id !== request?.id) {
        continue;
      }
      if (!`${event.kind} ${event.detail}`.toLowerCase().includes(query)) {
        continue;
      }
      const detail = createElement('details', undefined, 'event-row');
      detail.append(
        createElement('summary', `#${event.seq}  |  ${event.kind}`),
        createElement('pre', event.detail),
      );
      getElement('context-events').append(detail);
    }
  }
  render();
})();
