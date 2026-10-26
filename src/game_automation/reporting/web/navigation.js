(() => {
  'use strict';
  const current = new URL(location.href);
  const source = current.searchParams.get('from');
  const back = document.querySelector('[data-report-back]');
  function pageKey(value) {
    const url = new URL(value, location.href);
    url.searchParams.delete('from');
    return `${url.pathname}${url.search}`;
  }
  let managedReturn = false;
  try {
    managedReturn = Boolean(
      source && sessionStorage.getItem('game-auto-report-page') === pageKey(source),
    );
    sessionStorage.setItem('game-auto-report-page', pageKey(location.href));
  } catch {
    /* Explicit parent links remain usable when browser storage is unavailable. */
  }
  function safePage(value) {
    let target;
    try {
      target = new URL(value, location.href);
    } catch {
      return null;
    }
    const sameRoot = target.origin === location.origin;
    const page = /\/(?:tasks\/[^/]+\/.*|reports\/history\.html)$/.test(target.pathname);
    const liveTask = /\/tasks\/[^/]+\/$/.test(target.pathname);
    return sameRoot &&
      target.protocol === location.protocol &&
      page &&
      (target.pathname.endsWith('.html') || liveTask)
      ? target
      : null;
  }
  if (source && back) {
    const target = safePage(source);
    if (target && target.pathname !== current.pathname) back.href = target.href;
  } else if (back && new URL(back.href).pathname === current.pathname) {
    back.hidden = true;
  }
  back?.addEventListener('click', (event) => {
    if (managedReturn && history.length > 1) {
      event.preventDefault();
      history.back();
    }
  });
  if (location.protocol === 'file:')
    document.querySelectorAll('[data-live-view]').forEach((link) => {
      link.hidden = true;
    });
  document.addEventListener('click', (event) => {
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('data-report-back') || anchor.hasAttribute('download'))
      return;
    const target = new URL(anchor.href, location.href);
    if (target.origin !== current.origin || target.pathname === current.pathname) return;
    const action = anchor.closest('article[id]');
    if (action?.id.startsWith('action_') && !document.getElementById('trace-data'))
      history.replaceState(null, '', `#${action.id}`);
    if (target.pathname.endsWith('.html')) {
      const origin = new URL(location.href);
      origin.searchParams.delete('from');
      target.searchParams.set('from', `${origin.pathname}${origin.search}${origin.hash}`);
      anchor.href = target.href;
      anchor.removeAttribute('target');
    } else if (
      location.protocol.startsWith('http') &&
      /\.(?:json|txt|log|png|jpg|jpeg|webp)$/.test(target.pathname)
    ) {
      const base = new URL('../', location.href);
      if (!target.pathname.startsWith(base.pathname)) return;
      const reference = decodeURIComponent(target.pathname.slice(base.pathname.length));
      const query = new URLSearchParams({
        ref: reference,
        from: `${current.pathname}${current.search}${location.hash}`,
      });
      anchor.href = `evidence.html?${query}`;
      anchor.removeAttribute('target');
    }
  });
})();
