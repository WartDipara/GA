(() => {
  'use strict';
  let data = JSON.parse(document.getElementById('history-data').textContent);
  let offset = 0;
  const get = (id) => document.getElementById(`history-${id}`);
  const saved = new URLSearchParams(location.hash.slice(1));
  get('search').value = saved.get('q') || '';
  get('state').value = saved.get('state') || 'all';
  function remember() {
    const state = new URLSearchParams({ q: get('search').value, state: get('state').value });
    history.replaceState(null, '', `#${state}`);
  }
  function render() {
    const query = get('search').value.toLowerCase();
    const state = get('state').value;
    const visible = data.tasks.filter(
      (task) =>
        `${task.title} ${task.task_id}`.toLowerCase().includes(query) &&
        (state === 'all' || task[state] > 0),
    );
    offset = Math.min(offset, Math.max(0, Math.floor((visible.length - 1) / 50) * 50));
    get('rows').replaceChildren(
      ...visible.slice(offset, offset + 50).map((task) => {
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
          cell.textContent = value;
          row.append(cell);
        }
        return row;
      }),
    );
    get('count').textContent = `${visible.length} tasks`;
    get('previous').disabled = offset === 0;
    get('next').disabled = offset + 50 >= visible.length;
  }
  for (const id of ['search', 'state'])
    get(id).addEventListener(id === 'search' ? 'input' : 'change', () => {
      offset = 0;
      remember();
      render();
    });
  get('previous').addEventListener('click', () => {
    offset = Math.max(0, offset - 50);
    render();
  });
  get('next').addEventListener('click', () => {
    offset += 50;
    render();
  });
  async function refresh() {
    if (!location.protocol.startsWith('http')) return;
    try {
      const response = await fetch('history.json', {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error('Unavailable');
      data = await response.json();
      render();
      get('connection').textContent = 'Read-only polling / 3s';
    } catch {
      get('connection').textContent = 'Refresh unavailable; retained history';
    } finally {
      setTimeout(refresh, 3000);
    }
  }
  render();
  void refresh();
})();
