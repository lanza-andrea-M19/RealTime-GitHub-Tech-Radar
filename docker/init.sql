-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Raw GitHub events table
CREATE TABLE IF NOT EXISTS raw_github_events (
    id BIGSERIAL PRIMARY KEY,
    repo VARCHAR(255) NOT NULL,
    event_type VARCHAR(64) NOT NULL DEFAULT 'issue',
    issue_number INT NOT NULL,
    title TEXT NOT NULL,
    author VARCHAR(255),
    state VARCHAR(32) NOT NULL DEFAULT 'open',
    labels JSONB DEFAULT '[]'::jsonb,
    body TEXT,
    comments_count INT DEFAULT 0,
    upstream_created_at TIMESTAMPTZ NOT NULL,
    upstream_updated_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_repo_issue_event UNIQUE (repo, issue_number, event_type)
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_raw_events_repo ON raw_github_events(repo);
CREATE INDEX IF NOT EXISTS idx_raw_events_created ON raw_github_events(upstream_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_ingested ON raw_github_events(ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_state ON raw_github_events(state);

-- Issue vector embeddings table
CREATE TABLE IF NOT EXISTS issue_embeddings (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES raw_github_events(id) ON DELETE CASCADE,
    repo VARCHAR(255) NOT NULL,
    issue_number INT NOT NULL,
    title TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_event_embedding UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS idx_issue_embeddings_repo ON issue_embeddings(repo);
CREATE INDEX IF NOT EXISTS idx_issue_embeddings_vector ON issue_embeddings USING hnsw (embedding vector_cosine_ops);

