"""Tests for search_memory across backends."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pimemento import tools as T
from pimemento.backends.base import MemoryEntry
from pimemento.config import PimementoConfig


@pytest.fixture
def config(tmp_path):
    return PimementoConfig(memory_dir=tmp_path)


@pytest.fixture
def json_backend(config):
    from pimemento.backends.json_backend import JsonBackend

    return JsonBackend(config)


# ── JSON backend substring search ──


@pytest.mark.asyncio
async def test_json_search_finds_in_content(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=15K | client=Dupont", reason="from call",
    )
    result = await T.search_memory(json_backend, None, query="Dupont")
    assert "Dupont" in result
    assert "match" in result


@pytest.mark.asyncio
async def test_json_search_finds_in_reason(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=15K", reason="discussed during quarterly review",
    )
    result = await T.search_memory(json_backend, None, query="quarterly")
    assert "15K" in result


@pytest.mark.asyncio
async def test_json_search_respects_namespace_filter(json_backend, config):
    for ns in ["seo", "ads"]:
        await T.save_memory(
            json_backend, config, None,
            category="domain_context", type="insight",
            content=f"domain={ns}_info", reason="test",
            namespace=ns,
        )

    result = await T.search_memory(
        json_backend, None,
        query="domain", namespace="seo",
    )
    assert "seo_info" in result
    assert "ads_info" not in result


@pytest.mark.asyncio
async def test_json_search_respects_user_filter(json_backend, config):
    for user in ["sophie", "marc"]:
        await T.save_memory(
            json_backend, config, None,
            category="business_context", type="insight",
            content=f"note={user}_data", reason="test",
            user_id=user,
        )

    result = await T.search_memory(
        json_backend, None,
        query="note", user_id="sophie",
    )
    assert "sophie_data" in result
    assert "marc_data" not in result


# ── Mock backend with embedder ──


@pytest.mark.asyncio
async def test_search_with_embedder():
    """When embedder is available, query embedding is computed and passed."""
    mock_backend = AsyncMock()
    mock_backend.search = AsyncMock(return_value=[
        (MemoryEntry(content="budget=15K", category="business_context", type="insight", reason="test"), 0.92),
    ])

    mock_embedder = AsyncMock()
    mock_embedder.embed = AsyncMock(return_value=[0.1] * 384)

    config = PimementoConfig()
    result = await T.search_memory(
        mock_backend, mock_embedder,
        query="what is the budget",
    )

    mock_embedder.embed.assert_called_once_with("what is the budget")
    mock_backend.search.assert_called_once()
    call_kwargs = mock_backend.search.call_args
    assert call_kwargs.kwargs.get("query_embedding") is not None
    assert "0.92" in result
    assert "15K" in result


@pytest.mark.asyncio
async def test_search_without_embedder():
    """Without embedder, query_embedding is None."""
    mock_backend = AsyncMock()
    mock_backend.search = AsyncMock(return_value=[
        (MemoryEntry(content="budget=15K", category="business_context", type="insight", reason="test"), 1.0),
    ])

    result = await T.search_memory(
        mock_backend, None,
        query="budget",
    )

    call_kwargs = mock_backend.search.call_args
    assert call_kwargs.kwargs.get("query_embedding") is None
    assert "match" in result
