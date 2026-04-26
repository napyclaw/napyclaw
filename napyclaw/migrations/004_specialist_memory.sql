-- napyclaw/migrations/004_specialist_memory.sql
-- Adds specialist working memory table and per-specialist history window config.
-- Requires: 003_webchat.sql already applied.

ALTER TABLE group_contexts
    ADD COLUMN IF NOT EXISTS job_description  TEXT,
    ADD COLUMN IF NOT EXISTS verbatim_turns   INTEGER NOT NULL DEFAULT 7,
    ADD COLUMN IF NOT EXISTS summary_turns    INTEGER NOT NULL DEFAULT 5;

CREATE TABLE IF NOT EXISTS specialist_memory (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN (
                    'responsibility','task','tool','resource','preference','fact')),
    content     TEXT NOT NULL,
    embedding   vector(768),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS specialist_memory_group_idx
    ON specialist_memory (group_id);

CREATE INDEX IF NOT EXISTS specialist_memory_embedding_idx
    ON specialist_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
