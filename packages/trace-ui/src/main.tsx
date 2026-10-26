import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { workspaceView, type WorkspaceView } from '@game-automation/contracts/workspace';
import theme from '../../../config/ui-theme.json' with { type: 'json' };
import './workspace.css';

function duration(value: number | null): string {
  if (value === null) return 'Unknown';
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    trace: 'M5 3v12h14 M5 7h14 M5 11h9 M19 3v4 M14 7v4 M19 11v4 M5 15v6',
    sun: 'M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5 M16 12a4 4 0 1 1-8 0a4 4 0 1 1 8 0',
    arrow: 'M5 12h14 M13 6l6 6-6 6',
  };
  return (
    <svg className="ui-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d={paths[name] ?? paths['grid']} />
    </svg>
  );
}

function ThemeButton() {
  const [mode, setMode] = useState(() => {
    try {
      const saved = localStorage.getItem('game-auto-theme');
      return saved === 'light' || saved === 'dark' ? saved : theme.default_mode;
    } catch {
      return theme.default_mode;
    }
  });
  useEffect(() => {
    document.documentElement.dataset['theme'] = mode;
    try {
      localStorage.setItem('game-auto-theme', mode);
    } catch {
      // The selected theme still works when browser storage is disabled.
    }
  }, [mode]);
  return (
    <button className="ui-theme" onClick={() => setMode(mode === 'dark' ? 'light' : 'dark')}>
      <Icon name="sun" />
      {mode === 'dark' ? 'Light theme' : 'Dark theme'}
    </button>
  );
}

function Shell({ view }: { view: WorkspaceView }) {
  return (
    <>
      <aside className="ui-sidebar" aria-label="Workspace navigation">
        <a className="ui-logo" href={view.links[0]?.href ?? '#'}>
          <span className="ui-logo-mark">
            <Icon name="trace" />
          </span>
          <span>
            Game Automation<small>OBSERVABILITY</small>
          </span>
        </a>
        <p className="ui-nav-label">WORKSPACE</p>
        <nav className="ui-links">
          {view.links.map((link, index) => (
            <a key={link.href} href={link.href} aria-current={link.current ? 'page' : undefined}>
              <Icon name={index === 0 ? 'grid' : 'trace'} />
              {link.label}
            </a>
          ))}
        </nav>
        <div className="ui-sidebar-footer">
          <span className="ui-dot" />
          Local evidence store
          <small>Read-only workspace</small>
        </div>
      </aside>
      <div className="ui-topbar">
        <span>
          Workspace <span className="ui-separator">/</span>
          <strong>{view.links.find((link) => link.current)?.label ?? view.title}</strong>
        </span>
        <div>
          <span className="ui-local">
            <span className="ui-dot" />
            Local
          </span>
          <ThemeButton />
        </div>
      </div>
    </>
  );
}

function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <section className="ui-card">
      <div className="ui-card-heading">
        <h2>{title}</h2>
        <span>{note}</span>
      </div>
      {children}
    </section>
  );
}

function Distribution({ view }: { view: WorkspaceView }) {
  const total = view.states.reduce((sum, state) => sum + state.count, 0);
  let position = 0;
  const segments = view.states.map((state) => {
    const start = position;
    position += total ? (state.count / total) * 100 : 0;
    return `var(--${state.tone}) ${start}% ${position}%`;
  });
  return (
    <div className="ui-distribution">
      <div
        className="ui-donut"
        role="img"
        aria-label={`${total} recorded outcomes`}
        style={{ background: total ? `conic-gradient(${segments.join(',')})` : 'var(--border)' }}
      >
        <div>
          <strong>{total}</strong>
          <span>{view.kind === 'execution' ? 'calls' : 'executions'}</span>
        </div>
      </div>
      <div className="ui-legend">
        {view.states.map((state) => (
          <div key={state.label}>
            <span>
              <i style={{ background: `var(--${state.tone})` }} />
              {state.label}
            </span>
            <strong>{state.count}</strong>
          </div>
        ))}
        {!total && <p>No recorded outcomes yet.</p>}
      </div>
    </div>
  );
}

function Columns({ points }: { points: WorkspaceView['activity'] }) {
  const maximum = Math.max(1, ...points.map((point) => point.count));
  return (
    <div className="ui-columns" role="img" aria-label="Recorded counts by interval">
      {points.map((point) => (
        <div className="ui-column" key={point.label}>
          <strong>{point.count}</strong>
          <div className="ui-column-track">
            <span
              style={{ height: `${(point.count / maximum) * 100}%` }}
              title={`${point.label}: ${point.count}`}
            />
          </div>
          <small>{point.label.length > 8 ? point.label.slice(5) : point.label}</small>
        </div>
      ))}
      {!points.length && <p className="ui-empty">No recorded measurements yet.</p>}
    </div>
  );
}

function SlowCalls({ view }: { view: WorkspaceView }) {
  const maximum = Math.max(1, ...view.latency.map((call) => call.value ?? 0));
  return (
    <div className="ui-slow-calls">
      {view.latency.map((call) => (
        <a key={call.href} href={call.href}>
          <div>
            <span>{call.label}</span>
            <strong>
              {duration(call.value)}
              <Icon name="arrow" />
            </strong>
          </div>
          <div className="ui-bar-track">
            <span
              style={{
                width: `${((call.value ?? 0) / maximum) * 100}%`,
                background: `var(--${call.tone})`,
              }}
            />
          </div>
        </a>
      ))}
      {!view.latency.length && <p className="ui-empty">No measured calls yet.</p>}
    </div>
  );
}

function Insights({ initial }: { initial: WorkspaceView }) {
  const [view, setView] = useState(initial);
  const [connection, setConnection] = useState('Recorded snapshot');
  useEffect(() => {
    if (location.protocol === 'file:') return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const response = await fetch('workspace.json', { signal: controller.signal });
        if (!response.ok) throw new Error('Workspace unavailable');
        const value: unknown = await response.json();
        const next = workspaceView(value);
        if (!next) throw new Error('Invalid workspace');
        if (!controller.signal.aborted) {
          setView(next);
          setConnection('Live · refreshes every 3s');
        }
      } catch {
        if (!controller.signal.aborted) setConnection('Connection paused · retained snapshot');
      } finally {
        if (!controller.signal.aborted)
          timer = setTimeout(() => {
            void refresh();
          }, 3000);
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  const execution = view.kind === 'execution';
  return (
    <div className="ui-insights">
      <div className="ui-section-heading">
        <div>
          <p className="ui-kicker">{view.subtitle}</p>
          <h2>{execution ? 'Execution performance' : 'At a glance'}</h2>
        </div>
        <span className="ui-connection" role="status">
          <span className="ui-dot" />
          {connection}
        </span>
      </div>
      {!!view.summary.length && (
        <div className="ui-summary">
          {view.summary.map((metric) => (
            <div className="ui-card" key={metric.label}>
              <span>{metric.label}</span>
              <strong style={{ color: `var(--${metric.tone})` }}>
                {metric.value ?? 'Unknown'}
              </strong>
              <small>{metric.note}</small>
            </div>
          ))}
        </div>
      )}
      <div className="ui-chart-grid">
        <Panel
          title={execution ? 'Call latency distribution' : 'Execution activity'}
          note={execution ? 'Measured durations' : 'Recent recorded dates'}
        >
          {execution && (
            <div className="ui-percentiles">
              <div>
                <span>Median · P50</span>
                <strong>{duration(view.p50_ms)}</strong>
              </div>
              <div>
                <span>P95</span>
                <strong>{duration(view.p95_ms)}</strong>
              </div>
              <small>{view.missing_timings} unknown timings</small>
            </div>
          )}
          <Columns points={execution ? view.histogram : view.activity} />
        </Panel>
        <Panel
          title={execution ? 'Slowest module calls' : 'Execution outcomes'}
          note={execution ? 'Open a call to inspect' : 'Committed states'}
        >
          {execution ? <SlowCalls view={view} /> : <Distribution view={view} />}
        </Panel>
      </div>
      {execution && (
        <p className="ui-chart-note">
          Each bar measures one module call. Nested call durations overlap; unknown measurements are
          excluded.
        </p>
      )}
    </div>
  );
}

const source = document.getElementById('workspace-data');
try {
  const saved = localStorage.getItem('game-auto-theme');
  document.documentElement.dataset['theme'] =
    saved === 'light' || saved === 'dark' ? saved : theme.default_mode;
} catch {
  document.documentElement.dataset['theme'] = theme.default_mode;
}
const legacyThemeButton = document.getElementById('theme-toggle');
if (legacyThemeButton) {
  legacyThemeButton.textContent =
    document.documentElement.dataset['theme'] === 'light' ? 'Dark' : 'Light';
}
let initial: WorkspaceView | null = null;
try {
  const value: unknown = JSON.parse(source?.textContent ?? 'null');
  initial = workspaceView(value);
} catch {
  // Original report content remains available if the enhanced view cannot load.
}
if (initial) {
  const shell = document.createElement('div');
  shell.id = 'workspace-shell';
  document.body.prepend(shell);
  createRoot(shell).render(<Shell view={initial} />);
  document.body.classList.add('ui-ready');
  const main = document.querySelector('main');
  const before = main?.querySelector('nav, .history-toolbar, table');
  if (main && source?.dataset['charts'] === 'true') {
    const charts = document.createElement('div');
    main.insertBefore(charts, before?.parentElement === main ? before : null);
    createRoot(charts).render(<Insights initial={initial} />);
  }
}
