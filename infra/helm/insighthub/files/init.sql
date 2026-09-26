-- Fresh-volume starter schema. Existing volumes require an explicit migration/rebuild.
-- Do not reinterpret old vectors: their provider identity cannot be recovered safely.
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'documents'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'documents'
          AND column_name = 'content_sha256'
    ) THEN
        RAISE EXCEPTION 'Legacy InsightHub schema: migrate or rebuild explicitly before using this starter';
    END IF;
END $$;

-- One immutable vector space per starter index, claimed by the first successful ingest.
CREATE TABLE IF NOT EXISTS embedding_index (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    identity_id TEXT NOT NULL UNIQUE,
    identity JSONB NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed')),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    content_sha256 TEXT,
    pipeline_id TEXT,
    embedding_identity_id TEXT REFERENCES embedding_index(identity_id),
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, embedding_identity_id),
    CHECK (status <> 'ready' OR
        (chunk_count > 0 AND content_sha256 IS NOT NULL AND pipeline_id IS NOT NULL
         AND embedding_identity_id IS NOT NULL AND error_code IS NULL)),
    CHECK (status = 'ready' OR chunk_count = 0)
);

-- EMBEDDING_DIM must match VECTOR(1024). Dimension changes require rebuilding this schema.
CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    chunk_text TEXT NOT NULL CHECK (length(chunk_text) > 0),
    embedding VECTOR(1024) NOT NULL,
    embedding_identity_id TEXT NOT NULL REFERENCES embedding_index(identity_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index),
    FOREIGN KEY (document_id, embedding_identity_id)
        REFERENCES documents(id, embedding_identity_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents(status);
