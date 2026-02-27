"""Postgres + pgvector memory backend.

Requires asyncpg and pgvector. Install with:
    pip install pimemento[postgres]
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pimemento.backends.base import MemoryBackend, MemoryEntry
from pimemento.config import PimementoConfig

def _create_table_sql(dims: int) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS memories (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    client_id TEXT NOT NULL DEFAULT '_default',
    user_id TEXT DEFAULT '_anonymous',
    namespace TEXT NOT NULL DEFAULT 'general',
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{{}}',
    category TEXT,
    type TEXT CHECK (type IN ('decision', 'exclusion', 'insight', 'action', 'anomaly')),
    reason TEXT,
    embedding vector({dims}),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ,
    source_mcp TEXT,
    merged_from UUID[],
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', content || ' ' || COALESCE(reason, ''))
    ) STORED
)
"""

_CREATE_INDEXES_SQL = """
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_client') THEN
        CREATE INDEX idx_memories_client ON memories(client_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_client_ns') THEN
        CREATE INDEX idx_memories_client_ns ON memories(client_id, namespace);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_client_user') THEN
        CREATE INDEX idx_memories_client_user ON memories(client_id, user_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_metadata') THEN
        CREATE INDEX idx_memories_metadata ON memories USING gin(metadata);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_expires') THEN
        CREATE INDEX idx_memories_expires ON memories(expires_at) WHERE expires_at IS NOT NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding') THEN
        CREATE INDEX idx_memories_embedding ON memories
            USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_content_fts') THEN
        CREATE INDEX idx_memories_content_fts ON memories USING gin(content_tsv);
    END IF;
END $$;
"""


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _escape_ilike(s: str) -> str:
    """Escape ILIKE wildcard characters (%, _) to prevent wildcard injection."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_entry(row: Any) -> MemoryEntry:
    """Convert an asyncpg Record to MemoryEntry."""
    merged = row["merged_from"] or []
    return MemoryEntry(
        id=str(row["id"]),
        client_id=row["client_id"],
        user_id=row["user_id"] or "_anonymous",
        namespace=row["namespace"],
        content=row["content"],
        metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
        category=row["category"] or "",
        type=row["type"] or "",
        reason=row["reason"] or "",
        embedding=list(row["embedding"]) if row["embedding"] is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        source_mcp=row["source_mcp"] or "",
        merged_from=[str(uid) for uid in merged],
    )


class PostgresBackend(MemoryBackend):
    """Postgres + pgvector backend."""

    def __init__(self, config: PimementoConfig) -> None:
        self._dsn = config.database_url
        self._max_entries = config.max_entries_per_client
        self._embedding_dims = config.embedding_dimensions
        self._pool: Any = None  # asyncpg.Pool

    async def initialize(self) -> None:
        """Create connection pool and ensure schema exists."""
        import asyncpg
        from pgvector.asyncpg import register_vector

        async def _init_conn(conn):
            await register_vector(conn)

        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=2, max_size=10, init=_init_conn,
        )
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(_create_table_sql(self._embedding_dims))
            # Migration: add content_tsv column for existing tables
            await conn.execute("""
                ALTER TABLE memories ADD COLUMN IF NOT EXISTS
                    content_tsv tsvector GENERATED ALWAYS AS (
                        to_tsvector('simple', content || ' ' || COALESCE(reason, ''))
                    ) STORED
            """)
            await conn.execute(_CREATE_INDEXES_SQL)

    async def save(self, entry: MemoryEntry) -> MemoryEntry:
        async with self._pool.acquire() as conn:
            merged_uuids = [UUID(uid) for uid in entry.merged_from] if entry.merged_from else None

            row = await conn.fetchrow(
                """
                INSERT INTO memories (
                    id, client_id, user_id, namespace, content, metadata,
                    category, type, reason, embedding, created_at, updated_at,
                    expires_at, source_mcp, merged_from
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15
                )
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    category = EXCLUDED.category,
                    type = EXCLUDED.type,
                    reason = EXCLUDED.reason,
                    embedding = EXCLUDED.embedding,
                    updated_at = EXCLUDED.updated_at,
                    expires_at = EXCLUDED.expires_at,
                    source_mcp = EXCLUDED.source_mcp,
                    merged_from = EXCLUDED.merged_from
                RETURNING *
                """,
                UUID(entry.id),
                entry.client_id,
                entry.user_id,
                entry.namespace,
                entry.content,
                _json_dumps(entry.metadata),
                entry.category,
                entry.type,
                entry.reason,
                entry.embedding,
                entry.created_at,
                entry.updated_at,
                entry.expires_at,
                entry.source_mcp,
                merged_uuids,
            )

            # Enforce per-client entry cap (0 = unlimited)
            if not self._max_entries:
                return _row_to_entry(row)
            count = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE client_id = $1",
                entry.client_id,
            )
            if count > self._max_entries:
                await conn.execute(
                    """
                    DELETE FROM memories WHERE id IN (
                        SELECT id FROM memories
                        WHERE client_id = $1
                        ORDER BY updated_at ASC, id ASC
                        LIMIT $2
                    )
                    """,
                    entry.client_id,
                    count - self._max_entries,
                )

            return _row_to_entry(row)

    async def get(
        self,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
        category: str = "",
        type: str = "",
        limit: int = 20,
    ) -> list[MemoryEntry]:
        async with self._pool.acquire() as conn:
            conditions = ["client_id = $1", "(expires_at IS NULL OR expires_at >= now())"]
            params: list[Any] = [client_id]
            idx = 2

            if user_id:
                conditions.append(f"user_id = ${idx}")
                params.append(user_id)
                idx += 1
            if namespace:
                conditions.append(f"namespace = ${idx}")
                params.append(namespace)
                idx += 1
            if category:
                conditions.append(f"category = ${idx}")
                params.append(category)
                idx += 1
            if type:
                conditions.append(f"type = ${idx}")
                params.append(type)
                idx += 1

            where = " AND ".join(conditions)
            limit = max(1, min(limit, 100))
            params.append(limit)

            rows = await conn.fetch(
                f"""
                SELECT * FROM memories
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ${idx}
                """,
                *params,
            )
            return [_row_to_entry(r) for r in rows]

    async def delete(
        self,
        client_id: str,
        content_match: str,
        *,
        user_id: str = "",
        namespace: str = "",
        category: str = "",
    ) -> MemoryEntry | None:
        async with self._pool.acquire() as conn:
            conditions = [
                "client_id = $1",
                "content ILIKE $2",
            ]
            params: list[Any] = [client_id, f"%{_escape_ilike(content_match)}%"]
            idx = 3

            if user_id:
                conditions.append(f"user_id = ${idx}")
                params.append(user_id)
                idx += 1
            if namespace:
                conditions.append(f"namespace = ${idx}")
                params.append(namespace)
                idx += 1
            if category:
                conditions.append(f"category = ${idx}")
                params.append(category)
                idx += 1

            where = " AND ".join(conditions)
            row = await conn.fetchrow(
                f"""
                DELETE FROM memories WHERE id = (
                    SELECT id FROM memories
                    WHERE {where}
                    ORDER BY updated_at DESC
                    LIMIT 1
                )
                RETURNING *
                """,
                *params,
            )
            return _row_to_entry(row) if row else None

    async def status(
        self,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            filters: list[str] = []
            params: list[Any] = [client_id]
            idx = 2
            if user_id:
                filters.append(f"user_id = ${idx}")
                params.append(user_id)
                idx += 1
            if namespace:
                filters.append(f"namespace = ${idx}")
                params.append(namespace)
                idx += 1
            extra_filters = ""
            if filters:
                extra_filters = " AND " + " AND ".join(filters)

            row = await conn.fetchrow(
                f"""
                SELECT
                    count(*) as cnt,
                    array_agg(DISTINCT namespace) as namespaces,
                    array_agg(DISTINCT category) FILTER (WHERE category IS NOT NULL) as categories,
                    min(created_at)::date::text as oldest,
                    max(updated_at)::date::text as newest,
                    count(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at >= now()) as ttl_count
                FROM memories
                WHERE client_id = $1
                  AND (expires_at IS NULL OR expires_at >= now())
                  {extra_filters}
                """,
                *params,
            )

            if not row or row["cnt"] == 0:
                return {"count": 0}

            return {
                "count": row["cnt"],
                "namespaces": sorted(row["namespaces"] or []),
                "categories": sorted(filter(None, row["categories"] or [])),
                "oldest": row["oldest"] or "?",
                "newest": row["newest"] or "?",
                "ttl_count": row["ttl_count"],
            }

    async def search(
        self,
        query: str,
        client_id: str,
        *,
        user_id: str = "",
        namespace: str = "",
        limit: int = 10,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """Semantic search via pgvector cosine, or ILIKE fallback."""
        async with self._pool.acquire() as conn:
            conditions = [
                "client_id = $1",
                "(expires_at IS NULL OR expires_at >= now())",
            ]
            params: list[Any] = [client_id]
            idx = 2

            if user_id:
                conditions.append(f"user_id = ${idx}")
                params.append(user_id)
                idx += 1
            if namespace:
                conditions.append(f"namespace = ${idx}")
                params.append(namespace)
                idx += 1

            where = " AND ".join(conditions)

            if query_embedding is not None:
                # Cosine similarity search via pgvector
                emb_idx = idx
                limit_idx = idx + 1
                params.append(query_embedding)
                params.append(limit)
                rows = await conn.fetch(
                    f"""
                    SELECT *,
                           1 - (embedding <=> ${emb_idx}::vector) AS score
                    FROM memories
                    WHERE {where}
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> ${emb_idx}::vector
                    LIMIT ${limit_idx}
                    """,
                    *params,
                )
                return [(_row_to_entry(r), float(r["score"])) for r in rows]
            else:
                # Full-text search with ts_rank, then ILIKE fallback
                fts_idx = idx
                limit_idx = idx + 1
                fts_params = [*params, query, limit]
                rows = await conn.fetch(
                    f"""
                    SELECT *,
                           ts_rank(content_tsv, plainto_tsquery('simple', ${fts_idx})) AS score
                    FROM memories
                    WHERE {where}
                      AND content_tsv @@ plainto_tsquery('simple', ${fts_idx})
                    ORDER BY score DESC
                    LIMIT ${limit_idx}
                    """,
                    *fts_params,
                )
                if rows:
                    return [(_row_to_entry(r), float(r["score"])) for r in rows]

                # Fallback: ILIKE substring on content + reason
                ilike_pattern = f"%{_escape_ilike(query)}%"
                ilike_params = [*params, ilike_pattern, limit]
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM memories
                    WHERE {where}
                      AND (content ILIKE ${fts_idx} OR reason ILIKE ${fts_idx})
                    ORDER BY updated_at DESC
                    LIMIT ${limit_idx}
                    """,
                    *ilike_params,
                )
                return [(_row_to_entry(r), 0.5) for r in rows]

    async def find_duplicates(
        self,
        entry: MemoryEntry,
        threshold: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """Find semantically similar entries using pgvector cosine similarity.

        Scoped to (client_id, namespace, user_id) but NOT category,
        to catch cross-category semantic duplicates.
        """
        if entry.embedding is None:
            return []

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *,
                       1 - (embedding <=> $1::vector) AS score
                FROM memories
                WHERE client_id = $2
                  AND (expires_at IS NULL OR expires_at >= now())
                  AND namespace = $3
                  AND user_id = $4
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> $1::vector) >= $5
                ORDER BY score DESC
                LIMIT 5
                """,
                entry.embedding,
                entry.client_id,
                entry.namespace,
                entry.user_id,
                threshold,
            )
            return [(_row_to_entry(r), float(r["score"])) for r in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
