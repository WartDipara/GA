import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { workspaceView, type WorkspaceView } from '@game-automation/contracts/workspace';
import './workspace.css';
import './appearance.css';
import './layouts.css';
import { mountHistory } from './history.js';

mountHistory();

function duration(value: number | null): string {
  if (value === null) return 'Unknown';
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function ArrowIcon() {
  return (
    <svg
      className="ui-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <path d="M5 12h14 M13 6l6 6-6 6" />
    </svg>
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
            <span>
              {call.label}
              <small className="ui-call-date">
                {call.recorded_at ? new Date(call.recorded_at).toLocaleString(undefined, {
                  hour12: false,
                }) : 'Unknown time'}
                {call.sequence !== undefined ? ` · #${call.sequence}` : ''}
              </small>
            </span>
            <strong>
              {duration(call.value)}
              <ArrowIcon />
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
  const view = initial;
  const connection = 'Recorded snapshot · reload to update';
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
let initial: WorkspaceView | null = null;
try {
  const value: unknown = JSON.parse(source?.textContent ?? 'null');
  initial = workspaceView(value);
} catch {
  // Original report content remains available if the enhanced view cannot load.
}
if (initial) {
  const main = document.querySelector('main');
  const before = main?.querySelector('nav, .history-toolbar, table');
  if (main && source?.dataset['charts'] === 'true') {
    const charts = document.createElement('div');
    main.insertBefore(charts, before?.parentElement === main ? before : null);
    flushSync(() => createRoot(charts).render(<Insights initial={initial} />));
  }
}
