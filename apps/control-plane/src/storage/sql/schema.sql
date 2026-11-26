CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    submission_key TEXT UNIQUE,
    spec TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    cancel_requested INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    run_id TEXT REFERENCES runs(id),
    span_id TEXT,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS event_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS event_run_kind_seq ON events(run_id, kind, seq);
CREATE INDEX IF NOT EXISTS event_run_span_seq ON events(run_id, span_id, seq);
CREATE TABLE IF NOT EXISTS sessions (
    run_id TEXT,
    role TEXT,
    thread_id TEXT,
    PRIMARY KEY(run_id, role)
);
CREATE TABLE IF NOT EXISTS actions (
    run_id TEXT,
    action_id TEXT,
    status TEXT,
    result TEXT,
    PRIMARY KEY(run_id, action_id)
);
CREATE TABLE IF NOT EXISTS device_bindings (
    instance_id TEXT PRIMARY KEY,
    apk_key TEXT UNIQUE NOT NULL,
    instance_index INTEGER NOT NULL,
    run_id TEXT UNIQUE NOT NULL REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS gameplay_progress (
    workspace_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_inputs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    spec TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_aliases (
    alias_id TEXT PRIMARY KEY REFERENCES tasks(id),
    task_id TEXT NOT NULL REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS exploration_records (
    workspace_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id),
    data TEXT NOT NULL,
    PRIMARY KEY(workspace_key,kind,id)
);
CREATE INDEX IF NOT EXISTS exploration_task ON exploration_records(task_id,workspace_key,kind);
CREATE INDEX IF NOT EXISTS exploration_parent
    ON exploration_records(workspace_key,kind,json_extract(data,'$.parent_id'));
CREATE INDEX IF NOT EXISTS exploration_outgoing
    ON exploration_records(workspace_key,kind,json_extract(data,'$.data.from_page'));
CREATE INDEX IF NOT EXISTS exploration_status
    ON exploration_records(workspace_key,kind,json_extract(data,'$.status'));
CREATE INDEX IF NOT EXISTS exploration_key
    ON exploration_records(workspace_key,kind,json_extract(data,'$.data.key'));
CREATE INDEX IF NOT EXISTS exploration_action
    ON exploration_records(workspace_key,kind,json_extract(data,'$.data.action_id'));
