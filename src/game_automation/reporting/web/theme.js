(() => {
  'use strict';
  const root = document.documentElement;
  const key = 'game-auto-theme';

  function restore() {
    try {
      const saved = localStorage.getItem(key);
      if (saved === 'light' || saved === 'dark') root.dataset.theme = saved;
      else delete root.dataset.theme;
    } catch {
      // The configured CSS default remains available without browser storage.
    }
  }

  function mode() {
    return root.dataset.theme || getComputedStyle(root).colorScheme;
  }

  function label() {
    const button = document.getElementById('workspace-theme');
    if (button)
      button.querySelector('span').textContent = mode() === 'dark' ? 'Light theme' : 'Dark theme';
    const legacy = document.getElementById('theme-toggle');
    if (legacy) legacy.textContent = mode() === 'dark' ? 'Light' : 'Dark';
  }

  restore();
  document.addEventListener('DOMContentLoaded', () => {
    label();
    document.getElementById('workspace-theme')?.addEventListener('click', () => {
      const next = mode() === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try {
        localStorage.setItem(key, next);
      } catch {
        // The current page still applies the selected theme.
      }
      label();
    });
  });
  addEventListener('pageshow', () => {
    restore();
    label();
  });
  addEventListener('storage', (event) => {
    if (event.key === key || event.key === null) {
      restore();
      label();
    }
  });
})();
