"""Backend contract tests — same suite runs on JSON and Postgres.

Uses the parametrized `backend` fixture from conftest.py.
All client_ids are prefixed with 'contract_' for isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pimemento.backends.base import MemoryEntry


# ── save + get: round-trip ──


@pytest.mark.asyncio
async def test_save_and_get_all_fields(backend):
    """Save an entry with ALL fields populated, get it back, verify each field."""
    now = datetime.now(timezone.utc)
    entry = MemoryEntry(
        client_id="contract_allfields",
        user_id="alice",
        namespace="seo",
        content="budget=15K | timeline=Q2",
        metadata={"source": "call", "kv": {"budget": "15K", "timeline": "Q2"}},
        category="business_context",
        type="insight",
        reason="client shared budget",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=30),
        source_mcp="sales_mcp",
        merged_from=["old-uuid-1"],
    )
    saved = await backend.save(entry)

    assert saved.id == entry.id
    assert saved.content == "budget=15K | timeline=Q2"

    results = await backend.get("contract_allfields")
    assert len(results) == 1
    r = results[0]
    assert r.id == entry.id
    assert r.client_id == "contract_allfields"
    assert r.user_id == "alice"
    assert r.namespace == "seo"
    assert r.content == "budget=15K | timeline=Q2"
    assert r.category == "business_context"
    assert r.type == "insight"
    assert r.reason == "client shared budget"
    assert r.source_mcp == "sales_mcp"
    assert r.metadata.get("source") == "call"
    assert r.expires_at is not None
    assert r.merged_from == ["old-uuid-1"]


@pytest.mark.asyncio
async def test_save_upsert_updates_all_mutable_fields(backend):
    """Re-save same id with changed fields — all mutable fields must update."""
    now = datetime.now(timezone.utc)
    entry = MemoryEntry(
        client_id="contract_upsert",
        content="stack=React",
        category="project_config",
        type="decision",
        reason="initial",
        source_mcp="dev_mcp",
        metadata={"env": "staging"},
        created_at=now,
        updated_at=now,
    )
    saved = await backend.save(entry)
    entry_id = saved.id

    # Mutate every mutable field
    saved.content = "stack=Vue"
    saved.category = "business_context"
    saved.type = "insight"
    saved.reason = "changed mind"
    saved.source_mcp = "ops_mcp"
    saved.metadata = {"env": "production", "new_key": True}
    saved.expires_at = now + timedelta(days=7)
    saved.merged_from = ["merged-1", "merged-2"]
    saved.updated_at = now + timedelta(seconds=10)

    updated = await backend.save(saved)
    assert updated.id == entry_id

    # Verify via get (not just return value)
    results = await backend.get("contract_upsert")
    assert len(results) == 1
    r = results[0]
    assert r.content == "stack=Vue"
    assert r.category == "business_context"
    assert r.type == "insight"
    assert r.reason == "changed mind"
    assert r.source_mcp == "ops_mcp"
    assert r.metadata.get("env") == "production"
    assert r.metadata.get("new_key") is True
    assert r.expires_at is not None
    assert set(r.merged_from) == {"merged-1", "merged-2"}


@pytest.mark.asyncio
async def test_save_preserves_immutable_fields(backend):
    """After upsert, client_id stays the same."""
    entry = MemoryEntry(
        client_id="contract_immutable",
        content="data=1",
        category="project_config",
        type="insight",
        reason="test",
    )
    saved = await backend.save(entry)
    original_id = saved.id

    saved.content = "data=2"
    updated = await backend.save(saved)

    assert updated.id == original_id
    assert updated.client_id == "contract_immutable"

    results = await backend.get("contract_immutable")
    assert len(results) == 1
    assert results[0].client_id == "contract_immutable"


# ── get: filters ──


@pytest.mark.asyncio
async def test_get_filter_by_namespace(backend):
    for ns in ["seo", "ads"]:
        await backend.save(MemoryEntry(
            client_id="contract_ns",
            namespace=ns,
            content=f"channel={ns}",
            category="domain_context",
            type="insight",
            reason="test",
        ))

    results = await backend.get("contract_ns", namespace="seo")
    assert len(results) == 1
    assert results[0].namespace == "seo"


@pytest.mark.asyncio
async def test_get_filter_by_user_id(backend):
    for user in ["alice", "bob"]:
        await backend.save(MemoryEntry(
            client_id="contract_user",
            user_id=user,
            content=f"owner={user}",
            category="user_preference",
            type="insight",
            reason="test",
        ))

    results = await backend.get("contract_user", user_id="bob")
    assert len(results) == 1
    assert results[0].user_id == "bob"


@pytest.mark.asyncio
async def test_get_filter_by_category(backend):
    for cat in ["business_context", "project_config"]:
        await backend.save(MemoryEntry(
            client_id="contract_cat",
            content=f"cat={cat}",
            category=cat,
            type="insight",
            reason="test",
        ))

    results = await backend.get("contract_cat", category="project_config")
    assert len(results) == 1
    assert results[0].category == "project_config"


@pytest.mark.asyncio
async def test_get_filter_by_type(backend):
    for t in ["decision", "exclusion"]:
        await backend.save(MemoryEntry(
            client_id="contract_type",
            content=f"t={t}",
            category="business_context",
            type=t,
            reason="test",
        ))

    results = await backend.get("contract_type", type="exclusion")
    assert len(results) == 1
    assert results[0].type == "exclusion"


@pytest.mark.asyncio
async def test_get_limit(backend):
    now = datetime.now(timezone.utc)
    for i in range(5):
        await backend.save(MemoryEntry(
            client_id="contract_limit",
            content=f"item={i}",
            category="business_context",
            type="insight",
            reason="test",
            updated_at=now + timedelta(seconds=i),
        ))

    results = await backend.get("contract_limit", limit=2)
    assert len(results) == 2
    # Most recent first
    assert "4" in results[0].content
    assert "3" in results[1].content


@pytest.mark.asyncio
async def test_get_empty_client(backend):
    results = await backend.get("contract_nonexistent")
    assert results == []


# ── delete ──


@pytest.mark.asyncio
async def test_delete_by_content_match(backend):
    await backend.save(MemoryEntry(
        client_id="contract_del",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="test",
    ))
    await backend.save(MemoryEntry(
        client_id="contract_del",
        content="stack=React",
        category="project_config",
        type="decision",
        reason="test",
    ))

    removed = await backend.delete("contract_del", "budget")
    assert removed is not None
    assert "15K" in removed.content

    remaining = await backend.get("contract_del")
    assert len(remaining) == 1
    assert "React" in remaining[0].content


@pytest.mark.asyncio
async def test_delete_returns_none_when_not_found(backend):
    result = await backend.delete("contract_del_none", "xyz_no_match")
    assert result is None


@pytest.mark.asyncio
async def test_delete_respects_filters(backend):
    """Same content in 2 namespaces — delete with filter only removes targeted one."""
    for ns in ["seo", "ads"]:
        await backend.save(MemoryEntry(
            client_id="contract_delfilt",
            namespace=ns,
            content="budget=10K",
            category="business_context",
            type="insight",
            reason="test",
        ))

    removed = await backend.delete(
        "contract_delfilt", "budget", namespace="ads"
    )
    assert removed is not None
    assert removed.namespace == "ads"

    remaining = await backend.get("contract_delfilt")
    assert len(remaining) == 1
    assert remaining[0].namespace == "seo"


# ── status ──


@pytest.mark.asyncio
async def test_status_counts_and_metadata(backend):
    now = datetime.now(timezone.utc)
    entries = [
        MemoryEntry(
            client_id="contract_stat",
            namespace="seo",
            content="a=1",
            category="business_context",
            type="insight",
            reason="test",
            updated_at=now,
        ),
        MemoryEntry(
            client_id="contract_stat",
            namespace="seo",
            content="b=2",
            category="project_config",
            type="decision",
            reason="test",
            updated_at=now + timedelta(days=1),
        ),
        MemoryEntry(
            client_id="contract_stat",
            namespace="ads",
            content="c=3",
            category="business_context",
            type="insight",
            reason="test",
            updated_at=now + timedelta(days=2),
        ),
    ]
    for e in entries:
        await backend.save(e)

    info = await backend.status("contract_stat")
    assert info["count"] == 3
    assert "seo" in info["namespaces"]
    assert "ads" in info["namespaces"]
    assert "business_context" in info["categories"]
    assert "project_config" in info["categories"]


@pytest.mark.asyncio
async def test_status_empty(backend):
    info = await backend.status("contract_stat_empty")
    assert info["count"] == 0


@pytest.mark.asyncio
async def test_status_filters(backend):
    for ns in ["seo", "ads"]:
        await backend.save(MemoryEntry(
            client_id="contract_statfilt",
            namespace=ns,
            content=f"x={ns}",
            category="business_context",
            type="insight",
            reason="test",
        ))

    info = await backend.status("contract_statfilt", namespace="seo")
    assert info["count"] == 1
    assert info["namespaces"] == ["seo"]


# ── search ──


@pytest.mark.asyncio
async def test_search_substring_match(backend):
    await backend.save(MemoryEntry(
        client_id="contract_search",
        content="budget=15K | timeline=Q2",
        category="business_context",
        type="insight",
        reason="test",
    ))
    await backend.save(MemoryEntry(
        client_id="contract_search",
        content="stack=React | deploy=Vercel",
        category="project_config",
        type="decision",
        reason="test",
    ))

    results = await backend.search("budget", "contract_search")
    assert len(results) >= 1
    assert any("budget" in r[0].content.lower() for r in results)


@pytest.mark.asyncio
async def test_search_no_results(backend):
    results = await backend.search("zzz_no_match", "contract_search_empty")
    assert results == []


@pytest.mark.asyncio
async def test_search_respects_filters(backend):
    for ns in ["seo", "ads"]:
        await backend.save(MemoryEntry(
            client_id="contract_searchfilt",
            namespace=ns,
            content="budget=10K",
            category="business_context",
            type="insight",
            reason="test",
        ))

    results = await backend.search(
        "budget", "contract_searchfilt", namespace="ads"
    )
    assert len(results) == 1
    assert results[0][0].namespace == "ads"


# ── TTL / expiration ──


@pytest.mark.asyncio
async def test_expired_entries_excluded_from_get(backend):
    await backend.save(MemoryEntry(
        client_id="contract_ttl",
        content="old=data",
        category="business_context",
        type="insight",
        reason="test",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    await backend.save(MemoryEntry(
        client_id="contract_ttl",
        content="fresh=data",
        category="business_context",
        type="insight",
        reason="test",
    ))

    results = await backend.get("contract_ttl")
    assert len(results) == 1
    assert "fresh" in results[0].content


@pytest.mark.asyncio
async def test_expired_entries_excluded_from_search(backend):
    await backend.save(MemoryEntry(
        client_id="contract_ttlsearch",
        content="budget=expired",
        category="business_context",
        type="insight",
        reason="test",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    await backend.save(MemoryEntry(
        client_id="contract_ttlsearch",
        content="budget=current",
        category="business_context",
        type="insight",
        reason="test",
    ))

    results = await backend.search("budget", "contract_ttlsearch")
    assert len(results) == 1
    assert "current" in results[0][0].content


# ── close ──


@pytest.mark.asyncio
async def test_close_is_idempotent(backend):
    """Calling close() twice must not raise."""
    await backend.close()
    await backend.close()
