-- Operational tables for napyclaw (group state, tasks, logs)
-- Applied automatically by Docker on first init via docker-entrypoint-initdb.d

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    group_id     TEXT NOT NULL,
    sender_id    TEXT NOT NULL,
    sender_name  TEXT NOT NULL,
    text         TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    channel_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_contexts (
    group_id             TEXT PRIMARY KEY,
    default_name         TEXT NOT NULL,
    display_name         TEXT NOT NULL,
    nicknames            TEXT NOT NULL DEFAULT '[]',
    owner_id             TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    is_first_interaction BOOLEAN NOT NULL DEFAULT TRUE,
    history              TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id             TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL,
    owner_id       TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    schedule_type  TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    model          TEXT,
    provider       TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    next_run       TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_run_log (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    ran_at         TEXT NOT NULL,
    status         TEXT NOT NULL,
    result_snippet TEXT,
    duration_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS shield_log (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    detection_types TEXT NOT NULL DEFAULT '[]',
    timestamp       TEXT NOT NULL
);
