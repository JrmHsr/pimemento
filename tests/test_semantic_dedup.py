"""Tests for semantic dedup logic in tools.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from pimemento import tools as T
from pimemento.backends.base import MemoryEntry
from pimemento.config import PimementoConfig


@pytest.fixture
def config(tmp_path):
    return PimementoConfig(memory_dir=tmp_path, semantic_dedup_threshold=0.85)


@pytest.fixture
def mock_backend():
    backend = AsyncMock()
    backend.save = AsyncMock(side_effect=lambda e: e)
    return backend


@pytest.mark.asyncio
async def test_no_duplicates_inserts_new(mock_backend, config):
    """When find_duplicates returns empty, save as new entry."""
    mock_backend.find_duplicates = AsyncMock(return_value=[])

    result = await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="insight",
        content="budget=15K", reason="test",
    )
    assert "Saved." in result
    mock_backend.save.assert_called_once()


@pytest.mark.asyncio
async def test_key_dedup_merge(mock_backend, config):
    """Key-based dedup merges overlapping keys."""
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="AS=0 | persona=seniors",
        category="business_context",
        type="insight",
        reason="old",
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 1.0)])

    result = await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="decision",
        content="AS=12 | site=launched", reason="update",
    )
    assert "Updated" in result
    assert "as" in result.lower()  # shared key 'AS'

    saved_entry = mock_backend.save.call_args[0][0]
    assert "12" in saved_entry.content  # AS updated
    assert "persona" in saved_entry.content  # preserved
    assert "site" in saved_entry.content  # added
    assert saved_entry.type == "decision"  # type updated


@pytest.mark.asyncio
async def test_semantic_dedup_replaces_content(mock_backend, config):
    """Pure semantic match replaces content when no kv parseable."""
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="free text about budget",  # no key=value
        category="business_context",
        type="insight",
        reason="old",
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 0.92)])

    result = await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="insight",
        content="updated budget info",  # also no kv
        reason="new info",
    )
    assert "Updated" in result
    assert "semantic" in result

    saved_entry = mock_backend.save.call_args[0][0]
    assert saved_entry.content == "updated budget info"


@pytest.mark.asyncio
async def test_metadata_merge(mock_backend, config):
    """Metadata from existing and new entries are merged."""
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="old",
        metadata={"source": "call"},
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 1.0)])

    result = await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="insight",
        content="budget=20K", reason="update",
        metadata={"confidence": "high"},
    )
    assert "Updated" in result

    saved_entry = mock_backend.save.call_args[0][0]
    assert saved_entry.metadata["source"] == "call"  # preserved
    assert saved_entry.metadata["confidence"] == "high"  # added
    assert saved_entry.metadata["kv"]["budget"] == "20K"  # kv updated from content


@pytest.mark.asyncio
async def test_merged_from_tracking(mock_backend, config):
    """merged_from preserves history and records the incoming merged entry id."""
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="old",
        merged_from=["old-id"],
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 1.0)])

    await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="insight",
        content="budget=20K", reason="update",
    )

    saved_entry = mock_backend.save.call_args[0][0]
    assert "old-id" in saved_entry.merged_from
    assert len(saved_entry.merged_from) == 2
    assert any(mid != "old-id" for mid in saved_entry.merged_from)


@pytest.mark.asyncio
async def test_source_mcp_updated_on_merge(mock_backend, config):
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="old",
        source_mcp="sales",
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 1.0)])

    await T.save_memory(
        mock_backend, config, None,
        category="business_context", type="insight",
        content="budget=20K", reason="update",
        source_mcp="support",
    )

    saved_entry = mock_backend.save.call_args[0][0]
    assert saved_entry.source_mcp == "support"


@pytest.mark.asyncio
async def test_category_updated_on_merge(mock_backend, config):
    """Category must be updated to the new value during merge."""
    existing = MemoryEntry(
        id="existing-id",
        client_id="_default",
        content="budget=15K",
        category="business_context",
        type="insight",
        reason="old",
    )
    mock_backend.find_duplicates = AsyncMock(return_value=[(existing, 1.0)])

    await T.save_memory(
        mock_backend, config, None,
        category="project_config", type="decision",
        content="budget=20K", reason="reclassified",
    )

    saved_entry = mock_backend.save.call_args[0][0]
    assert saved_entry.category == "project_config"
