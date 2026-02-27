-- Pimemento: initial schema
-- Requires PostgreSQL 16+ with pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Multi-tenant + multi-user context
    client_id TEXT NOT NULL DEFAULT '_default',
    user_id TEXT DEFAULT '_anonymous',
    namespace TEXT NOT NULL DEFAULT 'general',

    -- Schema-less content
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',

    -- Classification
    category TEXT,
    type TEXT CHECK (type IN ('decision', 'exclusion', 'insight', 'action', 'anomaly')),
    reason TEXT,

    -- Semantic vector (384 dimensions for all-MiniLM-L6-v2)
    embedding vector(384),

    -- Lifecycle
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ,

    -- Traceability
    source_mcp TEXT,
    merged_from UUID[]
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_memories_client ON memories(client_id);
CREATE INDEX IF NOT EXISTS idx_memories_client_ns ON memories(client_id, namespace);
CREATE INDEX IF NOT EXISTS idx_memories_client_user ON memories(client_id, user_id);
CREATE INDEX IF NOT EXISTS idx_memories_metadata ON memories USING gin(metadata);
CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
