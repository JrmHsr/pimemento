"""Integration tests for Postgres backend.

Requires a running Postgres instance with pgvector.
Set DATABASE_URL to enable these tests:
    DATABASE_URL=postgresql://pimemento:pimemento@localhost:5432/pimemento pytest tests/test_postgres_backend.py
"""

from __future__ import annotations

import os

import pytest

from pimemento.backends.base import MemoryEntry
from pimemento.config import PimementoConfig

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set -- skipping Postgres integration tests",
)


@pytest.fixture
async def pg_backend():
    from pimemento.backends.postgres_backend import PostgresBackend

    config = PimementoConfig(
        backend="postgres",
        database_url=DATABASE_URL,
    )
    backend = PostgresBackend(config)
    await backend.initialize()

    # Clean up test data before each test
    async with backend._pool.acquire() as conn:
        await conn.execute("DELETE FROM memories WHERE client_id LIKE 'test_%'")

    yield backend
    await backend.close()


@pytest.mark.asyncio
async def test_save_and_get(pg_backend):
    entry = MemoryEntry(
        client_id="test_basic",
        content="stack=React | deploy=Vercel",
        category="project_config",
        type="insight",
        reason="user shared stack",
    )
    saved = await pg_backend.save(entry)
    assert saved.content == "stack=React | deploy=Vercel"

    results = await pg_backend.get("test_basic")
    assert len(results) == 1
    assert results[0].content == "stack=React | deploy=Vercel"


@pytest.mark.asyncio
async def test_status(pg_backend):
    entry = MemoryEntry(
        client_id="test_status",
        namespace="seo",
        content="test=data",
        category="domain_context",
        type="insight",
        reason="test",
    )
    await pg_backend.save(entry)

    info = await pg_backend.status("test_status")
    assert info["count"] == 1
    assert "seo" in info["namespaces"]


@pytest.mark.asyncio
async def test_status_namespace_filter(pg_backend):
    for ns in ["seo", "ads"]:
        await pg_backend.save(
            MemoryEntry(
                client_id="test_status_ns",
                namespace=ns,
                content=f"ctx={ns}",
                category="domain_context",
                type="insight",
                reason="test",
            )
        )

    info = await pg_backend.status("test_status_ns", namespace="seo")
    assert info["count"] == 1
    assert info["namespaces"] == ["seo"]


@pytest.mark.asyncio
async def test_delete(pg_backend):
    entry = MemoryEntry(
        client_id="test_delete",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="test",
    )
    await pg_backend.save(entry)

    removed = await pg_backend.delete("test_delete", "budget")
    assert removed is not None
    assert "15K" in removed.content

    results = await pg_backend.get("test_delete")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_ilike_search_fallback(pg_backend):
    """Search without embeddings uses ILIKE."""
    entry = MemoryEntry(
        client_id="test_search",
        content="budget=15K | timeline=Q2",
        category="business_context",
        type="insight",
        reason="test",
    )
    await pg_backend.save(entry)

    results = await pg_backend.search("budget", "test_search")
    assert len(results) == 1
    assert results[0][1] == 1.0


@pytest.mark.asyncio
async def test_ttl_prune(pg_backend):
    """Expired entries are pruned on get."""
    from datetime import datetime, timedelta

    entry = MemoryEntry(
        client_id="test_ttl",
        content="temp=data",
        category="business_context",
        type="insight",
        reason="test",
        expires_at=datetime.now() - timedelta(days=1),
    )
    await pg_backend.save(entry)

    results = await pg_backend.get("test_ttl")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_excludes_expired_entries(pg_backend):
    """Expired entries are excluded from search results."""
    from datetime import datetime, timedelta

    await pg_backend.save(
        MemoryEntry(
            client_id="test_search_ttl",
            content="budget=old",
            category="business_context",
            type="insight",
            reason="expired",
            expires_at=datetime.now() - timedelta(days=1),
        )
    )
    await pg_backend.save(
        MemoryEntry(
            client_id="test_search_ttl",
            content="budget=current",
            category="business_context",
            type="insight",
            reason="active",
        )
    )

    results = await pg_backend.search("budget", "test_search_ttl")
    assert len(results) == 1
    assert "current" in results[0][0].content


@pytest.mark.asyncio
async def test_find_duplicates_excludes_expired_entries(pg_backend):
    """Expired entries should not participate in semantic dedup."""
    from datetime import datetime, timedelta

    await pg_backend.save(
        MemoryEntry(
            client_id="test_dedup_ttl",
            content="stack=legacy",
            category="project_config",
            type="insight",
            reason="expired",
            expires_at=datetime.now() - timedelta(days=1),
            embedding=[0.1] * 384,
        )
    )

    incoming = MemoryEntry(
        client_id="test_dedup_ttl",
        content="stack=new",
        category="project_config",
        type="insight",
        reason="incoming",
        embedding=[0.1] * 384,
    )
    duplicates = await pg_backend.find_duplicates(incoming, 0.85)
    assert duplicates == []


@pytest.mark.asyncio
async def test_upsert_updates_source_mcp_and_category(pg_backend):
    """ON CONFLICT must update source_mcp and category, not just content."""
    entry = MemoryEntry(
        client_id="test_upsert",
        content="budget=10K",
        category="business_context",
        type="insight",
        reason="initial",
        source_mcp="sales",
    )
    saved = await pg_backend.save(entry)
    entry_id = saved.id

    # Re-save same id with updated source_mcp and category
    saved.source_mcp = "support"
    saved.category = "project_config"
    saved.content = "budget=20K"
    updated = await pg_backend.save(saved)

    assert updated.id == entry_id
    assert updated.source_mcp == "support"
    assert updated.category == "project_config"
    assert updated.content == "budget=20K"

    # Verify it persisted (not just returned from EXCLUDED)
    results = await pg_backend.get("test_upsert")
    assert len(results) == 1
    assert results[0].source_mcp == "support"
    assert results[0].category == "project_config"
