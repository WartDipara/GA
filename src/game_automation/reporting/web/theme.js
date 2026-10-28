(() => {
  'use strict';
  const root = document.documentElement;
  const themeKey = 'game-auto-theme';
  const styleKey = 'game-auto-style';
  const appearanceKey = 'game-auto-appearance';
  let configuration = null;
  let preferences = null;
  let styles = [];
  let preferredStyle = null;
  try {
    const value = JSON.parse(document.getElementById('appearance-config')?.textContent || 'null');
    if (
      value &&
      Number.isFinite(value.font_size) &&
      Number.isFinite(value.design_font_size) &&
      value.design_font_size > 0 &&
      Number.isFinite(value.minimum_text_ratio) &&
      Number.isFinite(value.min_font_size) &&
      Number.isFinite(value.max_font_size)
    ) {
      configuration = value;
      if (Array.isArray(value.styles))
        styles = value.styles.filter(
          (style) =>
            style &&
            typeof style.id === 'string' &&
            /^[a-z][a-z0-9-]{0,39}$/.test(style.id) &&
            ['balanced', 'reading', 'bento'].includes(style.layout),
        );
    }
  } catch {
    /* Older reports can retain their configured CSS defaults. */
  }

  function restore() {
    try {
      const saved = localStorage.getItem(themeKey);
      if (saved === 'light' || saved === 'dark') root.dataset.theme = saved;
      else delete root.dataset.theme;
      const savedStyle = localStorage.getItem(styleKey);
      preferredStyle = styles.some((style) => style.id === savedStyle) ? savedStyle : null;
      const value = JSON.parse(localStorage.getItem(appearanceKey) || 'null');
      preferences = configuration
        ? {
            fontSize:
              Number.isInteger(value?.fontSize) &&
              value.fontSize >= configuration.min_font_size &&
              value.fontSize <= configuration.max_font_size
                ? value.fontSize
                : configuration.font_size,
            reduceMotion:
              typeof value?.reduceMotion === 'boolean'
                ? value.reduceMotion
                : configuration.reduce_motion,
          }
        : null;
    } catch {
      preferredStyle = null;
      preferences = configuration
        ? { fontSize: configuration.font_size, reduceMotion: configuration.reduce_motion }
        : null;
    }
    apply();
  }

  function apply() {
    const style = styles.find(
      (candidate) => candidate.id === (preferredStyle || configuration?.default_style),
    );
    if (style) {
      root.dataset.style = style.id;
      root.dataset.layout = style.layout;
    } else {
      delete root.dataset.style;
      delete root.dataset.layout;
    }
    if (!preferences || !configuration) return;
    root.style.setProperty(
      '--font-scale',
      String(preferences.fontSize / configuration.design_font_size),
    );
    root.style.setProperty(
      '--font-min',
      `${preferences.fontSize * configuration.minimum_text_ratio}px`,
    );
    root.dataset.reduceMotion = String(preferences.reduceMotion);
  }

  function mode() {
    return root.dataset.theme || getComputedStyle(root).colorScheme;
  }

  function sync() {
    const button = document.getElementById('workspace-theme');
    if (button)
      button.querySelector('span').textContent = mode() === 'dark' ? 'Light theme' : 'Dark theme';
    const legacy = document.getElementById('theme-toggle');
    if (legacy) legacy.textContent = mode() === 'dark' ? 'Light' : 'Dark';
    const selector = document.getElementById('appearance-theme');
    if (selector) selector.value = root.dataset.theme || 'default';
    for (const choice of document.querySelectorAll('input[name="appearance-style"]'))
      choice.checked = choice.value === root.dataset.style;
    if (!preferences || !configuration) return;
    const size = document.getElementById('appearance-font-size');
    if (size) {
      size.min = configuration.min_font_size;
      size.max = configuration.max_font_size;
      size.value = preferences.fontSize;
    }
    const output = document.getElementById('appearance-font-value');
    if (output) output.value = `${preferences.fontSize}px`;
    const motion = document.getElementById('appearance-motion');
    if (motion) motion.checked = preferences.reduceMotion;
  }

  function save() {
    apply();
    sync();
    try {
      localStorage.setItem(appearanceKey, JSON.stringify(preferences));
    } catch {
      /* Settings still apply for this page when storage is unavailable. */
    }
  }

  function selectTheme(value) {
    if (value === 'light' || value === 'dark') root.dataset.theme = value;
    else delete root.dataset.theme;
    try {
      if (value === 'default') localStorage.removeItem(themeKey);
      else localStorage.setItem(themeKey, value);
    } catch {
      /* The current page still applies the selected theme. */
    }
    sync();
  }

  function selectStyle(value) {
    if (!styles.some((style) => style.id === value)) return;
    preferredStyle = value;
    try {
      localStorage.setItem(styleKey, value);
    } catch {
      /* The selected style still applies while this page is open. */
    }
    apply();
    sync();
  }

  function navigationState(restoreState) {
    const navigation = document.querySelector('#workspace-shell .ui-links');
    if (!navigation) return;
    try {
      if (restoreState) {
        const saved = JSON.parse(sessionStorage.getItem('game-auto-navigation') || 'null');
        for (const folder of navigation.querySelectorAll('details[data-nav-key]')) {
          const key = folder.dataset.navKey;
          if (typeof saved?.open?.[key] === 'boolean') folder.open = saved.open[key];
          if (folder.querySelector('[aria-current="page"]')) folder.open = true;
        }
        if (Number.isFinite(saved?.scroll)) navigation.scrollTop = saved.scroll;
      } else {
        const open = {};
        for (const folder of navigation.querySelectorAll('details[data-nav-key]'))
          open[folder.dataset.navKey] = folder.open;
        sessionStorage.setItem(
          'game-auto-navigation',
          JSON.stringify({ open, scroll: navigation.scrollTop }),
        );
      }
    } catch {
      /* Native links and disclosure controls remain usable. */
    }
  }

  restore();
  document.addEventListener('DOMContentLoaded', () => {
    sync();
    navigationState(true);
    const navigation = document.querySelector('#workspace-shell .ui-links');
    navigation?.addEventListener('toggle', () => navigationState(false), true);
    document
      .getElementById('workspace-theme')
      ?.addEventListener('click', () => selectTheme(mode() === 'dark' ? 'light' : 'dark'));
    const dialog = document.getElementById('appearance-settings');
    document.querySelectorAll('[data-appearance-open]').forEach((button) =>
      button.addEventListener('click', () => {
        sync();
        if (dialog && !dialog.open) dialog.showModal();
      }),
    );
    document
      .querySelector('[data-appearance-close]')
      ?.addEventListener('click', () => dialog?.close());
    document
      .getElementById('appearance-theme')
      ?.addEventListener('change', (event) => selectTheme(event.target.value));
    for (const choice of document.querySelectorAll('input[name="appearance-style"]'))
      choice.addEventListener('change', () => {
        if (choice.checked) selectStyle(choice.value);
      });
    document.getElementById('appearance-font-size')?.addEventListener('input', (event) => {
      if (!preferences || !configuration) return;
      const size = Number(event.target.value);
      if (
        !Number.isInteger(size) ||
        size < configuration.min_font_size ||
        size > configuration.max_font_size
      )
        return;
      preferences.fontSize = size;
      save();
    });
    document.querySelectorAll('[data-font-step]').forEach((button) =>
      button.addEventListener('click', () => {
        if (!preferences || !configuration) return;
        preferences.fontSize = Math.max(
          configuration.min_font_size,
          Math.min(
            configuration.max_font_size,
            preferences.fontSize + Number(button.dataset.fontStep),
          ),
        );
        save();
      }),
    );
    document.getElementById('appearance-motion')?.addEventListener('change', (event) => {
      if (!preferences) return;
      preferences.reduceMotion = event.target.checked;
      save();
    });
    document.getElementById('appearance-reset')?.addEventListener('click', () => {
      if (!configuration) return;
      preferences = {
        fontSize: configuration.font_size,
        reduceMotion: configuration.reduce_motion,
      };
      preferredStyle = null;
      try {
        localStorage.removeItem(styleKey);
      } catch {
        /* Reset still applies to the current page. */
      }
      selectTheme('default');
      save();
    });
  });
  addEventListener('pagehide', () => navigationState(false));
  addEventListener('pageshow', () => {
    restore();
    sync();
    navigationState(true);
  });
  addEventListener('storage', (event) => {
    if ([themeKey, styleKey, appearanceKey, null].includes(event.key)) {
      restore();
      sync();
    }
  });
})();
