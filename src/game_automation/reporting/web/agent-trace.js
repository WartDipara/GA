(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  let data = JSON.parse(byId('trace-data').textContent);
  let selected = data.spans[0].span_id;
  let activeView = 'calls';
  let selectedAction = '';
  let sessionBefore = 0;
  const collapsed = new Set();
  let busy = false;
  let refreshQueued = false;
  let refreshTimer;
  let refreshController;
  let closed = false;
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const number = (value) =>
    value === null || value === undefined ? 'Unavailable' : value.toLocaleString();
  const duration = (value) =>
    value === null || value === undefined
      ? 'Unmeasured'
      : value < 1000
        ? `${number(value)} ms`
        : `${(value / 1000).toFixed(2)} s`;
  const executionTimingDescription = (span) => {
    const timing = span.attributes['game_automation.execution_timing'];
    if (!timing) return '';
    if (timing.complete)
      return 'Sum of measured execution segments. Blocked waiting and report publication are excluded.';
    if (!timing.recorded_segments)
      return 'No execution segments were recorded. Total active execution time is unknown.';
    if (timing.unsegmented_execution)
      return 'Some execution activity has no segment record. Total active execution time is unknown.';
    if (timing.open_segments)
      return 'An execution segment has no recorded ending. Total active execution time is unknown.';
    return 'Some execution segments have missing or invalid timings. Total active execution time is unknown.';
  };
  const pretty = (value) => element('pre', JSON.stringify(value, null, 2));
  const button = (label, action) => {
    const node = element('button', label);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  };

  function remember() {
    const state = new URLSearchParams({ view: activeView, span: selected });
    state.set('q', byId('span-search').value);
    state.set('kind', byId('span-kind').value);
    state.set('status', byId('span-state').value);
    if (selectedAction) state.set('action', selectedAction);
    if (sessionBefore) state.set('session_before', String(sessionBefore));
    history.replaceState(null, '', `#${state}`);
  }

  function show(view, save = true) {
    if (!['calls', 'sessions', 'actions', 'performance', 'quality', 'insights'].includes(view))
      return;
    activeView = view;
    document
      .querySelectorAll('.view')
      .forEach((node) => node.classList.toggle('active', node.id === view));
    document
      .querySelectorAll('nav button')
      .forEach((node) => node.classList.toggle('active', node.dataset.view === view));
    if (save) remember();
    if (!loaded(view)) void refresh();
  }

  function loaded(view) {
    return !data.loaded_views || data.loaded_views.includes(view);
  }

  function detail(label, value) {
    const node = element('details');
    node.append(element('summary', label), pretty(value));
    return node;
  }

  function callEvidence(label, reference) {
    const node = element('details');
    node.append(element('summary', label));
    if (!reference) {
      node.append(element('p', 'No reference recorded.', 'muted'));
      return node;
    }
    const link = element('a', 'Open retained JSON');
    const raw = `../${reference}`;
    link.href =
      location.protocol === 'file:'
        ? raw
        : `evidence.html?${new URLSearchParams({ ref: reference })}`;
    node.append(link);
    let loaded = false;
    node.addEventListener('toggle', async () => {
      if (!node.open || loaded || location.protocol === 'file:') return;
      loaded = true;
      try {
        const response = await fetch(raw, { cache: 'no-store' });
        if (!response.ok) throw new Error('Unavailable');
        node.append(pretty(await response.json()));
      } catch {
        node.append(
          element(
            'p',
            'Evidence could not be loaded. The retained reference remains visible.',
            'muted',
          ),
        );
      }
    });
    return node;
  }

  function eventTimeline(span) {
    const node = element('details');
    node.append(element('summary', 'Recorded event timeline'));
    if (location.protocol === 'file:') {
      node.append(element('pre', `game-auto inspect ${data.run_id} --span ${span.span_id}`));
      node.append(element('p', 'Use serve-trace for on-demand event loading.'));
      return node;
    }
    let after = 0;
    let loaded = false;
    const more = button('Load more events', load);
    async function load() {
      more.disabled = true;
      try {
        const query = new URLSearchParams({ span: span.span_id, after: String(after) });
        const response = await fetch(`events.json?${query}`, { cache: 'no-store' });
        if (!response.ok) throw new Error('Unavailable');
        const page = await response.json();
        more.remove();
        page.events.forEach((event) => {
          node.append(detail(`#${event.seq} · ${event.kind}`, event));
        });
        after = page.next_after;
        if (page.has_more) node.append(more);
        if (!page.events.length) node.append(element('p', 'No events recorded for this node.'));
      } catch {
        node.append(
          element('p', 'Event evidence unavailable; use CLI inspect or the context view.', 'muted'),
        );
      } finally {
        more.disabled = false;
      }
    }
    node.addEventListener('toggle', () => {
      if (!node.open || loaded || location.protocol === 'file:') return;
      loaded = true;
      load();
    });
    return node;
  }

  function renderHeader() {
    byId('trace-limit').hidden = !data.event_limit_reached;
    const snapshot = data.snapshot;
    byId('snapshot-kind').textContent = snapshot
      ? `${snapshot.kind} / ${snapshot.business_status} · captured event #${snapshot.source_event_seq}`
      : 'Historical snapshot metadata unavailable';
    byId('execution-name').textContent = `${data.spans[0].name} / ${data.spans[0].status}`;
    byId('identity').textContent = `${data.trace_id} / ${data.run_id}`;
    const calls = data.spans.filter((span) => ['chat', 'tool'].includes(span.kind));
    const models = calls.filter((span) => span.kind === 'chat');
    const failed = calls.filter((span) => span.status === 'error').length;
    const metrics = [
      ['Model turns', models.length, 'App Server turns, not internal request count'],
      ['Tool calls', calls.length - models.length, 'Recorded framework boundaries'],
      ['Call errors', failed, 'Incomplete calls are shown separately'],
      ['Trace gaps', data.quality.automatic.unclosed_spans, 'Missing endings remain explicit'],
      [
        'Verified goals',
        data.quality.automatic.verified_business_goals,
        'Recorded business outcome evidence',
      ],
    ];
    byId('metrics').replaceChildren(
      ...metrics.map(([label, value, note]) => {
        const card = element('div', undefined, 'metric');
        card.append(
          element('span', label, 'muted'),
          element('strong', value),
          element('small', note),
        );
        return card;
      }),
    );
  }

  function renderTree() {
    const search = byId('span-search').value.toLowerCase();
    const kind = byId('span-kind').value;
    const state = byId('span-state').value;
    const children = new Map();
    const origin = data.spans.reduce((value, span) => {
      const start = Date.parse(span.start_time);
      return Number.isFinite(start) ? Math.min(value, start) : value;
    }, Infinity);
    const end = data.spans.reduce((value, span) => {
      const finish = Date.parse(span.end_time || span.start_time);
      return Number.isFinite(finish) ? Math.max(value, finish) : value;
    }, origin);
    const extent = Math.max(1, end - origin);
    data.spans.forEach((span) => {
      const parent = span.parent_span_id || '';
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(span);
    });
    const filtered = search || kind !== 'all' || state !== 'all';
    const rows = [];
    function walk(parent, depth, seen) {
      for (const span of children.get(parent) || []) {
        if (seen.has(span.span_id)) continue;
        const matches =
          (!search || JSON.stringify(span).toLowerCase().includes(search)) &&
          (kind === 'all' || span.kind === kind) &&
          (state === 'all' || span.status === state);
        if (matches) {
          const row = element(
            'div',
            undefined,
            `span-row${selected === span.span_id ? ' selected' : ''}`,
          );
          row.setAttribute('role', 'treeitem');
          row.setAttribute('aria-level', String(depth + 1));
          row.setAttribute('aria-selected', String(selected === span.span_id));
          if (children.has(span.span_id))
            row.setAttribute('aria-expanded', String(!collapsed.has(span.span_id)));
          row.tabIndex = 0;
          row.setAttribute(
            'aria-label',
            `${span.name}, ${span.status}, ${duration(span.duration_ms)}`,
          );
          const label = element('div', undefined, 'span-label');
          label.style.paddingLeft = `${10 + Math.min(depth, 8) * 16}px`;
          if (children.has(span.span_id)) {
            const caret = button(collapsed.has(span.span_id) ? '▸' : '▾', () => {
              if (collapsed.has(span.span_id)) collapsed.delete(span.span_id);
              else collapsed.add(span.span_id);
              renderTree();
            });
            caret.className = 'caret';
            caret.setAttribute('aria-label', `Toggle ${span.name} children`);
            caret.addEventListener('click', (event) => event.stopPropagation());
            label.append(caret);
          }
          label.append(element('span', span.kind, 'kind'), element('span', span.name, 'name'));
          const track = element('div', undefined, 'track');
          const bar = element('span', undefined, `bar ${span.kind} ${span.status}`);
          const start = Date.parse(span.start_time) - origin;
          const boundary =
            Date.parse(span.end_time || span.start_time) - Date.parse(span.start_time);
          bar.style.left = `${Math.max(0, (start / extent) * 100)}%`;
          bar.style.width = `${Math.max(0.4, Math.min(100, (boundary / extent) * 100))}%`;
          bar.title = `${span.name}: ${duration(span.duration_ms)}; ${span.status}`;
          const timingDescription = executionTimingDescription(span);
          if (timingDescription) bar.title += `. ${timingDescription}`;
          track.append(bar);
          row.append(label, track, element('span', duration(span.duration_ms), 'span-time'));
          const choose = () => {
            selected = span.span_id;
            remember();
            renderTree();
            renderInspector();
          };
          row.addEventListener('click', choose);
          row.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') choose();
          });
          rows.push(row);
        }
        if (filtered || !collapsed.has(span.span_id))
          walk(span.span_id, depth + 1, new Set([...seen, span.span_id]));
      }
    }
    walk('', 0, new Set());
    byId('waterfall').replaceChildren(...rows);
    if (!rows.length)
      byId('waterfall').append(element('p', 'No spans match these filters.', 'empty'));
  }

  function renderInspector() {
    const span = data.spans.find((entry) => entry.span_id === selected) || data.spans[0];
    const node = byId('span-inspector');
    node.dataset.disclosureScope = span.span_id;
    const metadata = element('dl');
    const timing = span.attributes['game_automation.execution_timing'];
    const fields = [
      ['Span', span.span_id],
      ['Parent', span.parent_span_id || 'Root'],
      [timing ? 'Active execution time' : 'Duration', duration(span.duration_ms)],
      ['Events', span.events.length],
    ];
    if (timing) {
      fields.push(['Measured segments', `${timing.measured_segments} / ${timing.recorded_segments}`]);
      if (!timing.complete && timing.measured_duration_ms !== null)
        fields.push(['Measured portion', duration(timing.measured_duration_ms)]);
    }
    fields.forEach(([label, value]) =>
      metadata.append(element('dt', label), element('dd', String(value))),
    );
    node.replaceChildren(
      element('span', span.status, `badge ${span.status}`),
      element('h3', span.name),
      metadata,
    );
    if (timing) node.append(element('p', executionTimingDescription(span)));
    if (span.parent_span_id) {
      const parent = element('a', 'Inspect parent call');
      parent.href = `#view=calls&span=${encodeURIComponent(span.parent_span_id)}`;
      node.append(parent);
    }
    if (span.attributes['game_automation.decision.reason_summary'])
      node.append(element('p', span.attributes['game_automation.decision.reason_summary']));
    node.append(
      callEvidence('Recorded input', span.attributes['game_automation.input_ref']),
      callEvidence('Recorded output', span.attributes['game_automation.output_ref']),
    );
    if (span.usage) node.append(detail('Token evidence and attribution', span.usage));
    if (span.cost) node.append(detail('OpenAI API reference estimate', span.cost));
    node.append(
      detail('Attributes', span.attributes),
      detail('Event references', span.events),
      eventTimeline(span),
    );
    span.links.forEach((link) => {
      if (link.run_id) {
        const anchor = element('a', `${link.relation}: ${link.run_id}`);
        anchor.href =
          `../../${encodeURIComponent(link.run_id)}/reports/trace.html` +
          (link.span_id ? `#view=calls&span=${encodeURIComponent(link.span_id)}` : '');
        const paragraph = element('p');
        paragraph.append(anchor);
        node.append(paragraph);
      } else node.append(detail(link.relation, link));
    });
    const action = data.actions.find((entry) => entry.span_id === span.span_id);
    if (action)
      node.append(
        button('Show action on screenshot', () => {
          selectedAction = action.action_id;
          show('actions');
          byId(action.action_id)?.scrollIntoView({ block: 'center' });
        }),
      );
  }

  function renderSessions() {
    const navigation = element('article', undefined, 'session-card');
    const timeline = data.replay.business_session;
    if (timeline) {
      navigation.append(element('h3', 'Business task session'));
      navigation.append(
        element(
          'p',
          'Execution batches and recovery segments; active timings exclude blocked waiting.',
          'muted',
        ),
      );
      for (const execution of timeline.executions) {
        const row = element('div', undefined, 'turn');
        const link = element('a', `${execution.mode} · ${execution.status} · ${execution.run_id}`);
        link.href = execution.href;
        row.append(link);
        if (!execution.total_segments)
          row.append(
            element(
              'p',
              'Recovery-segment measurements unavailable for this historical execution.',
              'muted',
            ),
          );
        for (const segment of execution.segments) {
          const attempt = element(
            'a',
            `${segment.attempt_id} · ${segment.status} · ${duration(segment.duration_ms)}`,
          );
          attempt.href = segment.href;
          const line = element('p');
          line.append(attempt);
          row.append(line);
        }
        if (execution.segments_truncated)
          row.append(
            element(
              'p',
              `Showing the latest 20 of ${execution.total_segments} segments; open the call trace for all segments.`,
              'muted',
            ),
          );
        navigation.append(row);
      }
      if (location.protocol !== 'file:') {
        const page = (before) => {
          sessionBefore = before;
          data.loaded_views = (data.loaded_views || []).filter((view) => view !== 'sessions');
          remember();
          void refresh();
        };
        if (timeline.before) navigation.append(button('Newest executions', () => page(0)));
        if (timeline.next_before !== null)
          navigation.append(button('Older executions', () => page(timeline.next_before)));
      }
    }
    const cards = data.replay.sessions.map((session) => {
      const card = element('article', undefined, 'session-card');
      card.append(element('h3', `Session ${session.session_id}`));
      session.turns.forEach((turn, index) => {
        const row = element('div', undefined, 'turn');
        row.dataset.disclosureScope = turn.span_id;
        const call = element('a', 'Inspect model call');
        call.href = `#view=calls&span=${encodeURIComponent(turn.span_id)}`;
        row.append(call);
        row.append(
          element('h3', `Turn ${index + 1} · ${turn.status}`),
          element('p', turn.turn_id || 'Turn ID unavailable', 'muted'),
        );
        row.append(
          detail('Submitted input preview', turn.submitted),
          callEvidence('Submitted input, Skills and attachments', turn.input_ref),
          detail('Public model items and provider Tool Calls', turn.public_items),
          callEvidence('Structured result', turn.output_ref),
        );
        const tools = element('details');
        tools.append(
          element('summary', `Related framework tools (${turn.framework_tools.length})`),
        );
        turn.framework_tools.forEach((tool) => {
          const entry = element('details');
          entry.append(
            element('summary', `${tool.name} · ${tool.status} · ${duration(tool.duration_ms)}`),
            callEvidence('Tool input', tool.input_ref),
            callEvidence('Tool output', tool.output_ref),
          );
          tools.append(entry);
        });
        row.append(tools, detail('Reported usage', turn.usage));
        card.append(row);
      });
      return card;
    });
    byId('session-list').replaceChildren(...(timeline ? [navigation] : []), ...cards);
    if (!cards.length)
      byId('session-list').append(
        element('p', 'No model sessions recorded in this execution.', 'empty'),
      );
  }

  function renderPerformance() {
    const groups = data.metrics.groups.filter(
      (row) =>
        (byId('model-filter').value === 'all' ||
          (row.model || 'unassigned') === byId('model-filter').value) &&
        (byId('actor-filter').value === 'all' ||
          (row.actor_ref || 'unassigned') === byId('actor-filter').value),
    );
    const table = element('table');
    const heading = element('tr');
    [
      'Agent',
      'Model',
      'Actor reference',
      'Calls',
      'Error rate',
      'P50',
      'P95',
      'Tokens / coverage',
      'Timing coverage',
    ].forEach((value) => heading.append(element('th', value)));
    table.append(heading);
    groups.forEach((group) => {
      const row = element('tr');
      [
        group.agent || 'Framework',
        group.model || 'Not applicable',
        group.actor_ref || 'Unassigned',
        `${group.calls} / ${group.incomplete} incomplete`,
        group.error_rate === null ? 'Unavailable' : `${(group.error_rate * 100).toFixed(1)}%`,
        duration(group.latency_p50_ms),
        duration(group.latency_p95_ms),
        `${number(group.attributed_tokens.totalTokens)} / ${group.usage_coverage}`,
        `${group.timing_coverage} timed`,
      ].forEach((value) => row.append(element('td', String(value))));
      table.append(row);
    });
    byId('performance-table').replaceChildren(table);
    byId('metric-basis').textContent = [
      data.metrics.error_rate_basis,
      data.metrics.latency_basis,
      data.metrics.usage_basis,
      data.metrics.cost_basis,
    ].join('. ');
    const referenceEstimates = data.metrics.groups
      .filter((group) => group.model)
      .map((group) => ({
        model: group.model,
        estimate_usd: group.api_reference_estimate_usd,
        coverage: group.cost_coverage,
      }));
    const price = data.spans.find((span) => span.cost)?.cost;
    byId('price-detail').replaceChildren(
      element('h3', 'Pricing basis'),
      pretty(price || { reason: 'No priced model turn recorded' }),
      pretty(referenceEstimates),
    );
  }

  function renderQuality() {
    const node = byId('quality-content');
    node.replaceChildren(
      detail('Automatic evaluators: operational facts', data.quality.automatic),
      detail('Human-reviewed quality and label coverage', data.quality.human),
      detail('Human annotations', data.quality.annotations),
      detail('Pinned implementation and Skill fingerprints', data.version),
    );
    const commands = `game-auto review ${data.run_id} --span <span_id> --metric tool_correctness --label pass --evidence <event_id> --note '<review note>'\ngame-auto export-dataset ${data.trace_id}\ngame-auto compare-traces ${data.run_id} <other_run_id>`;
    node.append(
      element('h3', 'Review, dataset and version comparison'),
      element(
        'p',
        'Reviews append evidence-backed labels and rebuild the local dataset. Unknown or disputed facts are not automatic ground truth.',
        'muted',
      ),
      element('pre', commands),
    );
  }

  function render() {
    function disclosureKey(node) {
      const scope = node.closest('[data-disclosure-scope], article[id]');
      const labels = [];
      let ancestor = node;
      while (ancestor?.tagName === 'DETAILS') {
        labels.unshift(ancestor.querySelector(':scope > summary')?.textContent || '');
        ancestor = ancestor.parentElement.closest('details');
      }
      return `${scope?.dataset.disclosureScope || scope?.id || node.closest('.view')?.id}/${labels.join('/')}`;
    }
    const open = new Set([...document.querySelectorAll('details[open]')].map(disclosureKey));
    renderHeader();
    renderTree();
    renderInspector();
    if (loaded('sessions')) renderSessions();
    else
      byId('session-list').replaceChildren(
        element('p', 'Open this view to load recorded turns.', 'muted'),
      );
    renderQuality();
    // These fragments are escaped application-rendered markup, never model-provided HTML.
    byId('action-list').innerHTML = loaded('actions')
      ? data.action_cards.join('') || '<p class="empty">No device actions in this execution.</p>'
      : '<p class="muted">Open this view to load recorded action evidence.</p>';
    byId('writer-health')?.replaceChildren(
      data.writer_health
        ? detail('Trace writer overhead · cumulative per collector', data.writer_health)
        : element('p', 'Writer overhead measurements are unavailable for this execution.', 'muted'),
    );
    for (const [id, values] of [
      ['model-filter', data.metrics.groups.map((row) => row.model)],
      ['actor-filter', data.metrics.groups.map((row) => row.actor_ref)],
    ]) {
      const selector = byId(id);
      const prior = selector.value;
      selector.replaceChildren(
        ...['all', ...new Set(values.map((value) => value || 'unassigned'))].map((value) => {
          const option = element('option', value === 'all' ? 'All' : value);
          option.value = value;
          return option;
        }),
      );
      selector.value = [...selector.options].some((option) => option.value === prior)
        ? prior
        : 'all';
    }
    renderPerformance();
    const results = byId('analysis-results');
    results.replaceChildren(
      ...data.analyses.map((analysis) => detail(`Analysis ${analysis.run_id}`, analysis)),
    );
    document.querySelectorAll('details').forEach((node) => {
      if (open.has(disclosureKey(node))) node.open = true;
    });
  }

  async function refresh() {
    if (closed || document.hidden || location.protocol === 'file:') return;
    if (busy) {
      refreshQueued = true;
      return;
    }
    clearTimeout(refreshTimer);
    busy = true;
    refreshController = new AbortController();
    try {
      const parameters = new URLSearchParams({
        after: String(loaded(activeView) ? (data.revision ?? -1) : -1),
      });
      if (['sessions', 'actions'].includes(activeView)) parameters.set('detail', activeView);
      if (activeView === 'sessions' && sessionBefore)
        parameters.set('before', String(sessionBefore));
      const response = await fetch(`agent-trace.json?${parameters}`, {
        cache: 'no-store',
        signal: refreshController.signal,
      });
      if (response.status !== 204) {
        if (!response.ok) throw new Error('Unavailable');
        const next = await response.json();
        if ((next.revision ?? 0) >= (data.revision ?? 0)) {
          if (next.revision === data.revision) {
            if (loaded('sessions') && !next.loaded_views.includes('sessions'))
              next.replay = data.replay;
            if (loaded('actions') && !next.loaded_views.includes('actions')) {
              next.actions = data.actions;
              next.action_cards = data.action_cards;
            }
            next.loaded_views = [...new Set([...(data.loaded_views || []), ...next.loaded_views])];
          }
          data = next;
          render();
          if (!loaded(activeView)) refreshQueued = true;
        }
      }
      byId('connection').textContent = 'Read-only polling / 3s';
    } catch {
      if (!refreshController.signal.aborted)
        byId('connection').textContent = 'Refresh unavailable; retained snapshot';
    } finally {
      busy = false;
      if (!closed && !document.hidden) {
        const delay = refreshQueued ? 0 : 3000;
        refreshQueued = false;
        refreshTimer = setTimeout(() => {
          void refresh();
        }, delay);
      }
    }
  }

  document
    .querySelectorAll('nav button')
    .forEach((node) => node.addEventListener('click', () => show(node.dataset.view)));
  ['span-search', 'span-kind', 'span-state'].forEach((id) =>
    byId(id).addEventListener(id === 'span-search' ? 'input' : 'change', () => {
      remember();
      renderTree();
    }),
  );
  ['model-filter', 'actor-filter'].forEach((id) =>
    byId(id).addEventListener('change', renderPerformance),
  );
  byId('expand-all').addEventListener('click', () => {
    collapsed.clear();
    renderTree();
  });
  byId('collapse-all').addEventListener('click', () => {
    data.spans
      .filter((span) => ['step', 'chain'].includes(span.kind))
      .forEach((span) => collapsed.add(span.span_id));
    renderTree();
  });
  byId('show-markers').addEventListener('change', () =>
    byId('actions').classList.toggle('hide-markers', !byId('show-markers').checked),
  );
  byId('refresh').addEventListener('click', refresh);
  byId('analysis-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const question = byId('analysis-question').value.replaceAll("'", "''");
    byId('analysis-command').textContent =
      `game-auto analyze-trace ${data.run_id} --question '${question}'`;
  });
  function restore() {
    const state = new URLSearchParams(location.hash.slice(1));
    selected = state.get('span') || data.spans[0].span_id;
    selectedAction = state.get('action') || '';
    const before = Number(state.get('session_before') || 0);
    if (Number.isSafeInteger(before) && before >= 0 && before !== sessionBefore) {
      sessionBefore = before;
      if (data.loaded_views)
        data.loaded_views = data.loaded_views.filter((view) => view !== 'sessions');
    }
    byId('span-search').value = state.get('q') || '';
    byId('span-kind').value = state.get('kind') || 'all';
    byId('span-state').value = state.get('status') || 'all';
    render();
    show(state.get('view') || 'calls', false);
    if (selectedAction && activeView === 'actions')
      byId(selectedAction)?.scrollIntoView({ block: 'center' });
  }
  document.addEventListener('click', (event) => {
    const action = event.target.closest('.action-card[id]');
    if (!action) return;
    selectedAction = action.id;
    selected = action.dataset.span || selected;
    remember();
  });
  addEventListener('hashchange', restore);
  restore();
  addEventListener('pagehide', () => {
    closed = true;
    clearTimeout(refreshTimer);
    refreshController?.abort();
  });
  addEventListener('pageshow', () => {
    closed = false;
    void refresh();
  });
  document.addEventListener('visibilitychange', () => {
    clearTimeout(refreshTimer);
    if (document.hidden) refreshController?.abort();
    else void refresh();
  });
  refreshTimer = setTimeout(() => {
    void refresh();
  }, 3000);
})();
