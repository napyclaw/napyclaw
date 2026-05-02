-- napyclaw/migrations/003_webchat.sql
-- Adds webchat-specific columns to group_contexts.
-- Requires: 002_operational.sql already applied.

ALTER TABLE group_contexts
    ADD COLUMN IF NOT EXISTS job_title      TEXT,
    ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS channel_type   TEXT NOT NULL DEFAULT 'slack';
