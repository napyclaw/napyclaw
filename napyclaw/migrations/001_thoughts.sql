-- PostgreSQL + pgvector schema for VectorMemory backend (Plan 5)
-- Apply with: psql $VECTOR_DB_URL -f napyclaw/migrations/001_thoughts.sql

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS thoughts (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    content    TEXT        NOT NULL,
    embedding  vector(768),           -- nomic-embed-text produces 768-dim vectors
    group_id   TEXT,                  -- NULL = global memory; set = group-scoped
    user_id    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS thoughts_embedding_idx
    ON thoughts USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS thoughts_group_idx ON thoughts (group_id);

-- Returns union of group-scoped + global thoughts ordered by cosine similarity
CREATE OR REPLACE FUNCTION match_thoughts(
    query_embedding vector(768),
    p_group_id      TEXT,
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    id         UUID,
    content    TEXT,
    similarity FLOAT
)
LANGUAGE SQL STABLE AS $$
    SELECT id, content, 1 - (embedding <=> query_embedding) AS similarity
    FROM   thoughts
    WHERE  group_id = p_group_id OR group_id IS NULL
    ORDER  BY embedding <=> query_embedding
    LIMIT  match_count;
$$;
