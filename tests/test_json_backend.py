"""Tests for JSON backend."""

from __future__ import annotations

import pytest

from pimemento.backends.base import MemoryEntry
from pimemento.backends.json_backend import JsonBackend
from pimemento.config import PimementoConfig


@pytest.mark.asyncio
async def test_save_and_get(json_backend):
    entry = MemoryEntry(
        client_id="test",
        content="stack=React | deploy=Vercel",
        category="project_config",
        type="insight",
        reason="user shared stack",
    )
    saved = await json_backend.save(entry)
    assert saved.content == "stack=React | deploy=Vercel"

    results = await json_backend.get("test")
    assert len(results) == 1
    assert results[0].content == "stack=React | deploy=Vercel"
    assert results[0].category == "project_config"


@pytest.mark.asyncio
async def test_get_empty(json_backend):
    results = await json_backend.get("nonexistent")
    assert results == []


@pytest.mark.asyncio
async def test_user_id_isolation(json_backend):
    for user in ["sophie", "marc"]:
        entry = MemoryEntry(
            client_id="acme",
            user_id=user,
            content=f"note={user}_note",
            category="business_context",
            type="insight",
            reason="test",
        )
        await json_backend.save(entry)

    # Get all
    all_entries = await json_backend.get("acme")
    assert len(all_entries) == 2

    # Filter by user
    sophie = await json_backend.get("acme", user_id="sophie")
    assert len(sophie) == 1
    assert "sophie_note" in sophie[0].content


@pytest.mark.asyncio
async def test_namespace_isolation(json_backend):
    for ns in ["seo", "ads"]:
        entry = MemoryEntry(
            client_id="test",
            namespace=ns,
            content=f"domain={ns}_data",
            category="domain_context",
            type="insight",
            reason="test",
        )
        await json_backend.save(entry)

    seo = await json_backend.get("test", namespace="seo")
    assert len(seo) == 1
    assert "seo_data" in seo[0].content


@pytest.mark.asyncio
async def test_key_dedup(json_backend):
    """Key-based dedup: overlapping keys trigger merge."""
    entry1 = MemoryEntry(
        client_id="test",
        content="AS=0 | persona=seniors",
        category="business_context",
        type="insight",
        reason="initial",
    )
    await json_backend.save(entry1)

    # New entry with overlapping key 'AS'
    entry2 = MemoryEntry(
        client_id="test",
        content="AS=12 | site=launched",
        category="business_context",
        type="insight",
        reason="update",
    )
    duplicates = await json_backend.find_duplicates(entry2, 0.85)
    assert len(duplicates) == 1
    assert duplicates[0][1] == 1.0  # score is 1.0 for key match


@pytest.mark.asyncio
async def test_no_dedup_different_category(json_backend):
    """No dedup across different categories."""
    entry1 = MemoryEntry(
        client_id="test",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="test",
    )
    await json_backend.save(entry1)

    entry2 = MemoryEntry(
        client_id="test",
        content="budget=20K",
        category="project_config",
        type="insight",
        reason="test",
    )
    duplicates = await json_backend.find_duplicates(entry2, 0.85)
    assert len(duplicates) == 0


@pytest.mark.asyncio
async def test_max_entries_cap(tmp_path):
    """Oldest entries dropped when over cap."""
    from datetime import datetime, timedelta

    cap = 10
    cfg = PimementoConfig(memory_dir=tmp_path, max_entries_per_client=cap)
    capped_backend = JsonBackend(cfg)

    for i in range(cap + 5):
        entry = MemoryEntry(
            client_id="test",
            content=f"item={i}",
            category="business_context",
            type="insight",
            reason="test",
            created_at=datetime.now() + timedelta(seconds=i),
            updated_at=datetime.now() + timedelta(seconds=i),
        )
        await capped_backend.save(entry)

    results = await capped_backend.get("test", limit=100)
    assert len(results) <= cap


@pytest.mark.asyncio
async def test_delete_most_recent(json_backend):
    """Delete removes the most recent matching entry."""
    from datetime import datetime, timedelta

    for i in range(3):
        entry = MemoryEntry(
            client_id="test",
            content=f"budget={i * 10}K",
            category="business_context",
            type="insight",
            reason=f"version {i}",
            created_at=datetime.now() + timedelta(seconds=i),
            updated_at=datetime.now() + timedelta(seconds=i),
        )
        await json_backend.save(entry)

    removed = await json_backend.delete("test", "budget")
    assert removed is not None
    assert "20K" in removed.content  # most recent

    results = await json_backend.get("test")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_delete_not_found(json_backend):
    removed = await json_backend.delete("test", "nonexistent")
    assert removed is None


@pytest.mark.asyncio
async def test_substring_search(json_backend):
    """search_memory uses substring fallback on JSON backend."""
    entry = MemoryEntry(
        client_id="test",
        content="budget=15K | timeline=Q2",
        category="business_context",
        type="insight",
        reason="client shared budget",
    )
    await json_backend.save(entry)

    results = await json_backend.search("budget", "test")
    assert len(results) == 1
    assert results[0][1] == 1.0  # substring match score
    assert "15K" in results[0][0].content


@pytest.mark.asyncio
async def test_search_no_results(json_backend):
    results = await json_backend.search("nonexistent", "test")
    assert results == []


@pytest.mark.asyncio
async def test_status(json_backend):
    entry = MemoryEntry(
        client_id="test",
        namespace="seo",
        content="test=data",
        category="domain_context",
        type="insight",
        reason="test",
    )
    await json_backend.save(entry)

    info = await json_backend.status("test")
    assert info["count"] == 1
    assert "seo" in info["namespaces"]
    assert "domain_context" in info["categories"]


@pytest.mark.asyncio
async def test_status_empty(json_backend):
    info = await json_backend.status("nonexistent")
    assert info["count"] == 0


@pytest.mark.asyncio
async def test_status_namespace_filter(json_backend):
    for ns in ["seo", "ads"]:
        await json_backend.save(
            MemoryEntry(
                client_id="status_ns",
                namespace=ns,
                content=f"ctx={ns}",
                category="domain_context",
                type="insight",
                reason="test",
            )
        )

    info = await json_backend.status("status_ns", namespace="seo")
    assert info["count"] == 1
    assert info["namespaces"] == ["seo"]


@pytest.mark.asyncio
async def test_load_corrupt_json_logs_warning(json_backend, caplog):
    import logging

    path = json_backend._path("corrupt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")

    caplog.set_level(logging.WARNING)
    results = await json_backend.get("corrupt")
    assert results == []
    assert "Failed to load JSON memory file" in caplog.text


@pytest.mark.asyncio
async def test_load_corrupt_json_creates_backup_and_resets_file(json_backend):
    import json

    path = json_backend._path("corrupt_backup")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")

    results = await json_backend.get("corrupt_backup")
    assert results == []

    backups = list(path.parent.glob("memory.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{bad json"
    assert json.loads(path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_prune_expired(json_backend):
    """Expired entries are pruned on read."""
    from datetime import datetime, timedelta

    entry = MemoryEntry(
        client_id="test",
        content="temp=data",
        category="business_context",
        type="insight",
        reason="test",
        expires_at=datetime.now() - timedelta(days=1),  # already expired
    )
    await json_backend.save(entry)

    results = await json_backend.get("test")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_prune_expired_timezone_aware(json_backend):
    """Expired timezone-aware entries are pruned correctly."""
    from datetime import datetime, timedelta, timezone

    entry = MemoryEntry(
        client_id="test_tz",
        content="temp=data",
        category="business_context",
        type="insight",
        reason="test",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    await json_backend.save(entry)

    results = await json_backend.get("test_tz")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_memory_dir_accepts_string(tmp_path):
    """JsonBackend accepts config.memory_dir provided as str."""
    from pimemento.backends.json_backend import JsonBackend
    from pimemento.config import PimementoConfig

    backend = JsonBackend(PimementoConfig(memory_dir=str(tmp_path)))
    entry = MemoryEntry(
        client_id="string_dir",
        content="k=v",
        category="business_context",
        type="insight",
        reason="test",
    )
    await backend.save(entry)

    results = await backend.get("string_dir")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_backward_compat_date_field(json_backend):
    """Handles legacy 'date' field from v2.0."""
    import json
    from pathlib import Path

    path = json_backend._path("legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_entry = {
        "namespace": "general",
        "category": "business_context",
        "type": "insight",
        "content": "old=data",
        "reason": "legacy",
        "date": "2024-01-15T10:00:00",
    }
    path.write_text(json.dumps([legacy_entry]), encoding="utf-8")

    results = await json_backend.get("legacy")
    assert len(results) == 1
    assert results[0].content == "old=data"


@pytest.mark.asyncio
async def test_backward_compat_ttl_days(json_backend):
    """Handles legacy 'ttl_days' field from v2.0."""
    import json
    from pathlib import Path

    path = json_backend._path("legacy_ttl")
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_entry = {
        "namespace": "general",
        "category": "business_context",
        "type": "insight",
        "content": "temp=data",
        "reason": "legacy",
        "date": "2020-01-01T10:00:00",  # old date
        "ttl_days": 30,
    }
    path.write_text(json.dumps([legacy_entry]), encoding="utf-8")

    results = await json_backend.get("legacy_ttl")
    assert len(results) == 0  # expired via ttl_days
