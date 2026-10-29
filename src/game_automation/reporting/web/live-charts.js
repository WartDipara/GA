/* Bounded charts for recorded events; no full-trace reconstruction. */
function createLiveCharts(container) {
  const definitions = [
    { id: 'calls', title: 'Call activity', unit: 'Calls / 10 seconds',
      labels: ['All calls started', 'Model calls started', 'Failed / incomplete calls'] },
    { id: 'latency', title: 'Measured call duration', unit: 'Seconds / completed call',
      labels: ['Mean duration', 'Maximum duration'] },
    { id: 'actions', title: 'Action outcomes', unit: 'Results / 10 seconds',
      labels: ['Verified / reconciled', 'Executed, awaiting verification', 'Rejected / unknown'] },
  ];
  let retained = [];
  let frame;
  let closed = false;
  const views = new Map();
  const localTime = (value) => new Date(value).toLocaleString(undefined, { hour12: false });
  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function svgNode(tag, attributes, text) {
    const result = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, value);
    if (text !== undefined) result.textContent = text;
    return result;
  }
  for (const definition of definitions) {
    const card = node('article', undefined, 'panel monitor-chart');
    const heading = node('div', undefined, 'panel-heading');
    const expand = node('button', 'Expand');
    expand.type = 'button';
    expand.setAttribute('aria-expanded', 'false');
    expand.setAttribute('aria-label', `Expand ${definition.title.toLowerCase()}`);
    expand.addEventListener('click', () => {
      const expanded = card.classList.toggle('expanded');
      expand.textContent = expanded ? 'Restore' : 'Expand';
      expand.setAttribute('aria-expanded', String(expanded));
      schedule();
    });
    heading.append(node('h2', definition.title), expand);
    const plot = node('div', undefined, 'chart-plot');
    const legend = node('div', undefined, 'chart-legend');
    definition.labels.forEach((label, index) => {
      const item = node('span', label, `series-${index}`);
      item.prepend(node('i', undefined, 'chart-swatch'));
      legend.append(item);
    });
    const note = node('p', 'Waiting for recorded samples.', 'panel-footnote');
    card.append(heading, node('p', definition.unit, 'chart-unit'), plot, legend, note);
    container.append(card);
    views.set(definition.id, { plot, note });
  }

  function render() {
    if (closed) return;
    const timestamps = retained.map((event) => Date.parse(event.timestamp));
    const latest = Math.max(...timestamps);
    const start = Math.floor(Math.max(Math.min(...timestamps), latest - 1190000) / 10000) * 10000;
    const end = Math.floor(latest / 10000) * 10000;
    const buckets = retained.length ? Array.from(
      { length: Math.floor((end - start) / 10000) + 1 },
      () => ({ calls: [0, 0, 0], latency: [], actions: [0, 0, 0] }),
    ) : [];
    for (const event of retained) {
      const bucket = buckets[Math.floor((Date.parse(event.timestamp) - start) / 10000)];
      if (!bucket) continue;
      if (event.kind === 'call.started') {
        bucket.calls[0] += 1;
        if (event.label === 'ai.decide') bucket.calls[1] += 1;
      }
      if (/^call\.(failed|incomplete)$/.test(event.kind)) bucket.calls[2] += 1;
      if (event.kind === 'call.completed' && Number.isFinite(event.duration_ms))
        bucket.latency.push(event.duration_ms / 1000);
      if (event.kind === 'action.result' && !event.truncated) {
        try {
          const status = JSON.parse(event.detail).status;
          if (['verified', 'reconciled'].includes(status)) bucket.actions[0] += 1;
          else if (status === 'executed') bucket.actions[1] += 1;
          else if (['rejected', 'unknown'].includes(status)) bucket.actions[2] += 1;
        } catch {
          // Invalid historical payloads are not counted as outcomes.
        }
      }
    }
    for (const definition of definitions) {
      const { plot, note } = views.get(definition.id);
      if (!retained.length) {
        plot.replaceChildren(node('p', 'No recorded samples in this window.', 'empty-state'));
        note.textContent = 'Unknown data is not a zero measurement.';
        continue;
      }
      const values = buckets.map((bucket) => {
        if (definition.id !== 'latency') return bucket[definition.id];
        const measured = bucket.latency.length ? [
          bucket.latency.reduce((sum, value) => sum + value, 0) / bucket.latency.length,
          Math.max(...bucket.latency),
        ] : [null, null];
        return measured;
      });
      const maximum = Math.max(1, ...values.flat().filter(Number.isFinite));
      const step = definition.id === 'latency' ? maximum / 4 : Math.max(1, Math.ceil(maximum / 4));
      const ceiling = step * 4;
      const width = Math.max(300, plot.clientWidth);
      const fontSize = parseFloat(getComputedStyle(plot).fontSize) || 14;
      const left = Math.max(64, fontSize * 5);
      const right = width - 24;
      const expanded = plot.closest('.monitor-chart').classList.contains('expanded');
      const bottom = Math.max(expanded ? 360 : 240, fontSize * (expanded ? 20 : 14));
      const height = bottom + fontSize * 5;
      const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
        'aria-label': `${definition.title}. X axis: local time. Y axis: ${definition.unit}.` });
      svg.style.height = `${height}px`;
      const formatValue = (value) => value.toLocaleString(undefined, { maximumFractionDigits: 2 });
      for (let tick = 0; tick <= 4; tick += 1) {
        const y = bottom - tick / 4 * (bottom - 20);
        svg.append(svgNode('path', { d: `M${left} ${y}H${right}`, class: 'chart-grid' }),
          svgNode('text', { x: left - 10, y: y + fontSize / 3, 'text-anchor': 'end' },
            formatValue(tick * step)));
      }
      const tickCount = width < fontSize * 42 ? 2 : width < fontSize * 65 ? 3 : 5;
      for (let tick = 0; tick < tickCount; tick += 1) {
        const ratio = tick / (tickCount - 1);
        const time = new Date(start + ratio * (end - start));
        svg.append(svgNode('text', { x: left + ratio * (right - left), y: bottom + fontSize * 2,
          'text-anchor': tick === 0 ? 'start' : tick === tickCount - 1 ? 'end' : 'middle' },
        time.toLocaleTimeString(undefined, { hour12: false })));
      }
      svg.append(svgNode('text', { x: (left + right) / 2, y: height - fontSize,
        'text-anchor': 'middle' }, 'Time (local)'));
      definition.labels.forEach((label, series) => {
        let path = '';
        let connected = false;
        const points = [];
        values.forEach((value, index) => {
          const sample = value[series];
          if (!Number.isFinite(sample)) { connected = false; return; }
          const x = left + index / Math.max(1, values.length - 1) * (right - left);
          const y = bottom - sample / ceiling * (bottom - 20);
          path += `${connected ? 'L' : 'M'}${x} ${y} `;
          connected = true;
          if (sample > 0 || definition.id === 'latency') {
            const point = svgNode('circle', { cx: x, cy: y, r: 3, class: `chart-point series-${series}` });
            point.append(svgNode('title', {},
              `${localTime(start + index * 10000)} · ${label}: ${formatValue(sample)}`));
            points.push(point);
          }
        });
        svg.append(svgNode('path', { d: path, class: `chart-line series-${series}` }), ...points);
      });
      plot.replaceChildren(svg);
      note.textContent = `${localTime(start)} – ${localTime(end + 10000)}. ` +
        (definition.id === 'latency' ? 'Completed calls only; gaps mean no measured sample.' :
          'Counts from retained records only; not task totals.');
    }
  }
  function schedule() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(render);
  }
  const widths = new WeakMap();
  const resize = new ResizeObserver((entries) => {
    let changed = false;
    for (const entry of entries) {
      if (widths.get(entry.target) !== entry.contentRect.width) changed = true;
      widths.set(entry.target, entry.contentRect.width);
    }
    if (changed) schedule();
  });
  for (const { plot } of views.values()) resize.observe(plot);
  addEventListener('pagehide', () => { closed = true; cancelAnimationFrame(frame); });
  addEventListener('pageshow', () => { closed = false; schedule(); });
  const themeObserver = new MutationObserver(schedule);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'data-style'] });
  return (events, runId) => {
    retained = events.filter((event) => (!runId || event.run_id === runId) &&
      Number.isFinite(Date.parse(event.timestamp)) &&
      (/^call\.(started|completed|failed|incomplete)$/.test(event.kind) || event.kind === 'action.result'));
    schedule();
  };
}
