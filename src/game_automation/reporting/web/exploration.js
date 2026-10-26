(() => {
  'use strict';
  const get = (id) => document.getElementById(id);
  let data = JSON.parse(get('catalog-data').textContent);
  let offset = 0;
  let busy = false;
  let timer;
  const size = 100;
  function node(tag, value, className = '') {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    element.className = className;
    return element;
  }
  function inspect(record) {
    const locations = data.locations
      .filter((location) => location.element_id === record.id)
      .slice(0, 10);
    const memory = data.records.filter(
      (item) => item.kind === 'locator' && item.parent_id === record.id,
    );
    get('detail').textContent = JSON.stringify({ element: record, memory, locations }, null, 2);
  }
  function render() {
    const pages = new Map(
      data.records.filter((record) => record.kind === 'page').map((record) => [record.id, record]),
    );
    const elements = data.records.filter((record) => record.kind === 'element');
    const selectedPage = get('page').value;
    get('page').replaceChildren(
      new Option('All pages', ''),
      ...[...pages.values()].map((page) => new Option(page.data.label, page.id)),
    );
    get('page').value = selectedPage;
    const selectedStatus = get('status').value;
    get('status').replaceChildren(
      new Option('All states', ''),
      ...Object.keys(data.counts).map((status) => new Option(status, status)),
    );
    get('status').value = selectedStatus;
    get('scope').textContent = `${data.view} · ${data.scope}`;
    get('metrics').replaceChildren(
      ...[
        ['Pages', pages.size],
        ['Elements', elements.length],
        ['Verified', data.counts.verified || 0],
        ['Pending', (data.counts.pending || 0) + (data.counts.in_progress || 0)],
        [
          'Gaps',
          ['locked', 'prohibited', 'limited', 'unknown'].reduce(
            (total, state) => total + (data.counts[state] || 0),
            0,
          ),
        ],
      ].map(([label, value]) => {
        const card = node('div', undefined, 'metric');
        card.append(node('span', label), node('strong', value));
        return card;
      }),
    );
    const query = get('search').value.toLowerCase();
    const visible = elements.filter(
      (record) =>
        (!selectedPage || record.parent_id === selectedPage) &&
        (!selectedStatus || record.status === selectedStatus) &&
        JSON.stringify(record.data).toLowerCase().includes(query),
    );
    offset = Math.min(offset, Math.max(0, Math.floor((visible.length - 1) / size) * size));
    get('elements').replaceChildren(
      ...visible.slice(offset, offset + size).map((record) => {
        const row = node('tr');
        const page = node('td', pages.get(record.parent_id)?.data.label || 'Unknown page');
        page.append(node('small', record.data.container));
        const target = node('td');
        const choose = node('button', record.data.label);
        choose.addEventListener('click', () => inspect(record));
        target.append(choose, node('small', record.data.item || record.data.kind));
        const state = node('td');
        state.append(node('span', record.status, `badge ${record.status}`));
        const bounds = node('td', record.data.bounds.join(', '));
        const action = node('td');
        if (record.data.action_id) {
          const link = node('a', 'Evidence');
          link.href = `../../${record.run_id}/reports/gameplay.html#${record.data.action_id}`;
          action.append(link);
        }
        row.append(page, target, state, bounds, action);
        return row;
      }),
    );
    get('count').textContent =
      `${visible.length ? offset + 1 : 0}–${Math.min(offset + size, visible.length)} of ${visible.length} elements`;
    get('previous').disabled = offset === 0;
    get('next').disabled = offset + size >= visible.length;
    get('edges').replaceChildren(
      ...data.records
        .filter((record) => record.kind === 'edge')
        .slice(-200)
        .map((record) =>
          node(
            'div',
            `${pages.get(record.data.from_page)?.data.label || 'Unknown'} → ${pages.get(record.data.to_page)?.data.label || 'Unknown'} · ${record.data.action_type}`,
            'relation',
          ),
        ),
    );
    get('scrolls').replaceChildren(
      ...data.records
        .filter((record) => record.kind === 'scroll')
        .map((record) =>
          node(
            'div',
            `${record.status} · ${record.data.viewports.length} viewports · ${record.data.reason}`,
            'relation',
          ),
        ),
    );
  }
  async function refresh() {
    if (busy || !location.protocol.startsWith('http')) return;
    clearTimeout(timer);
    busy = true;
    try {
      const response = await fetch('exploration.json', {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      data = await response.json();
      render();
      get('connection').textContent = 'Polling / 3s';
    } catch (error) {
      get('connection').textContent = `Refresh unavailable: ${error.message}`;
    } finally {
      busy = false;
      timer = setTimeout(refresh, 3000);
    }
  }
  for (const id of ['search', 'page', 'status'])
    get(id).addEventListener(id === 'search' ? 'input' : 'change', () => {
      offset = 0;
      render();
    });
  get('previous').addEventListener('click', () => {
    offset = Math.max(0, offset - size);
    render();
  });
  get('next').addEventListener('click', () => {
    offset += size;
    render();
  });
  get('refresh').addEventListener('click', refresh);
  render();
  void refresh();
})();
