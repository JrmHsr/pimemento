"""Tests for tools.py business logic."""

from __future__ import annotations

import pytest

from pimemento import tools as T
from pimemento.config import PimementoConfig


@pytest.fixture
def config(tmp_path):
    return PimementoConfig(memory_dir=tmp_path)


@pytest.fixture
def json_backend(config):
    from pimemento.backends.json_backend import JsonBackend

    return JsonBackend(config)


# ── save_memory validation ──


@pytest.mark.asyncio
async def test_save_missing_category(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="", type="insight", content="x=1", reason="test",
    )
    assert "Error: category required" in result


@pytest.mark.asyncio
async def test_save_missing_content(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight", content="", reason="test",
    )
    assert "Error: content required" in result


@pytest.mark.asyncio
async def test_save_missing_reason(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight", content="x=1", reason="",
    )
    assert "Error: reason required" in result


@pytest.mark.asyncio
async def test_save_invalid_type(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="invalid", content="x=1", reason="test",
    )
    assert "Error: type 'invalid' invalid" in result


@pytest.mark.asyncio
async def test_save_nonstandard_category_warning(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="weird_category", type="insight", content="x=1", reason="test",
    )
    assert "non-standard" in result


@pytest.mark.asyncio
async def test_save_custom_category_no_warning(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="x_medical", type="insight", content="x=1", reason="test",
    )
    assert "non-standard" not in result
    assert "Saved." in result


@pytest.mark.asyncio
async def test_save_no_kv_warning(json_backend, config):
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="just plain text", reason="test",
    )
    assert "no key=value detected" in result


@pytest.mark.asyncio
async def test_save_content_truncation(json_backend, config):
    long_content = "k=" + "x" * 1000
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content=long_content, reason="test",
    )
    assert "Saved." in result
    entries = await json_backend.get("_default")
    assert len(entries[0].content) <= config.max_content_len


# ── save_memory dedup ──


@pytest.mark.asyncio
async def test_save_key_dedup_merge(json_backend, config):
    """Key-based dedup merges content."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="AS=0 | persona=seniors", reason="initial",
    )

    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="AS=12 | site=launched", reason="update",
    )
    assert "Updated" in result
    assert "as" in result.lower()  # shared key mentioned

    entries = await json_backend.get("_default")
    assert len(entries) == 1
    content = entries[0].content
    assert "12" in content  # AS updated to 12
    assert "persona" in content  # persona preserved
    assert "site" in content  # site added


@pytest.mark.asyncio
async def test_save_auto_parses_kv_into_metadata(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="project_config", type="insight",
        content="stack=React+Node | deploy=Vercel",
        reason="initial capture",
        metadata={"source": "call"},
    )

    entries = await json_backend.get("_default")
    assert len(entries) == 1
    assert entries[0].metadata["source"] == "call"
    assert entries[0].metadata["kv"]["stack"] == "React+Node"
    assert entries[0].metadata["kv"]["deploy"] == "Vercel"


@pytest.mark.asyncio
async def test_save_merge_updates_metadata_kv(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="project_config", type="insight",
        content="stack=React | ci=gha",
        reason="initial",
        metadata={"source": "brief"},
    )
    await T.save_memory(
        json_backend, config, None,
        category="project_config", type="decision",
        content="stack=React+Node | deploy=Vercel",
        reason="update",
        metadata={"confidence": "high"},
    )

    entries = await json_backend.get("_default")
    assert len(entries) == 1
    meta = entries[0].metadata
    assert meta["source"] == "brief"
    assert meta["confidence"] == "high"
    assert meta["kv"]["stack"] == "React+Node"
    assert meta["kv"]["ci"] == "gha"
    assert meta["kv"]["deploy"] == "Vercel"


# ── get_memory ──


@pytest.mark.asyncio
async def test_get_empty(json_backend):
    result = await T.get_memory(json_backend, client_id="empty")
    assert "No memory" in result


@pytest.mark.asyncio
async def test_get_formatting(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="decision",
        content="stack=React", reason="team decided",
        user_id="sophie",
    )
    result = await T.get_memory(json_backend, client_id="_default")
    assert "DECISION" in result
    assert "business_context" in result
    assert "stack=React" in result
    assert "@sophie" in result


# ── delete_memory ──


@pytest.mark.asyncio
async def test_delete(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=15K", reason="test",
    )
    result = await T.delete_memory(json_backend, content_match="budget")
    assert "Deleted" in result
    assert "15K" in result


@pytest.mark.asyncio
async def test_delete_not_found(json_backend):
    result = await T.delete_memory(json_backend, content_match="nope")
    assert "No entry" in result


@pytest.mark.asyncio
async def test_delete_empty_match(json_backend):
    result = await T.delete_memory(json_backend, content_match="")
    assert "Error" in result


# ── memory_status ──


@pytest.mark.asyncio
async def test_status_empty(json_backend):
    result = await T.memory_status(json_backend, client_id="empty")
    assert "No memory" in result


@pytest.mark.asyncio
async def test_status_format(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="x=1", reason="test",
        namespace="seo",
    )
    result = await T.memory_status(json_backend, client_id="_default")
    assert "1 entries" in result
    assert "seo" in result
    assert "business_context" in result


@pytest.mark.asyncio
async def test_status_namespace_filter(json_backend, config):
    for ns in ["seo", "ads"]:
        await T.save_memory(
            json_backend, config, None,
            category="domain_context", type="insight",
            content=f"ctx={ns}", reason="test",
            namespace=ns,
        )

    result = await T.memory_status(
        json_backend,
        client_id="_default",
        namespace="seo",
    )
    assert "1 entries" in result
    assert "seo" in result
    assert "ads" not in result


# ── search_memory ──


@pytest.mark.asyncio
async def test_search_empty_query(json_backend):
    result = await T.search_memory(json_backend, None, query="")
    assert "Error: query required" in result


@pytest.mark.asyncio
async def test_search_substring(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=15K | timeline=Q2", reason="client shared",
    )
    result = await T.search_memory(json_backend, None, query="budget")
    assert "15K" in result
    assert "match" in result  # score is 1.0 -> "match"


@pytest.mark.asyncio
async def test_search_no_results(json_backend, config):
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="x=1", reason="test",
    )
    result = await T.search_memory(json_backend, None, query="zzzzz")
    assert "No results" in result


# ── conflict detection on save ──


@pytest.mark.asyncio
async def test_save_merge_reports_changed_values(json_backend, config):
    """When merging keys with different values, the response reports the change."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="alice said",
    )
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=40K", reason="bob said",
    )
    assert "Updated" in result
    assert "50K" in result
    assert "40K" in result
    assert "changed" in result.lower()


@pytest.mark.asyncio
async def test_save_merge_reports_user_info(json_backend, config):
    """When merging, the previous user is mentioned in the change report."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="first",
        user_id="alice",
    )
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=40K", reason="update",
        user_id="alice",
    )
    assert "Updated" in result
    assert "@alice" in result


@pytest.mark.asyncio
async def test_save_merge_no_change_report_when_same_value(json_backend, config):
    """When key value is the same, no 'changed' in response."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="first",
    )
    result = await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K | timeline=Q2", reason="add timeline",
    )
    assert "Updated" in result
    assert "changed" not in result.lower()


# ── conflict detection on read ──


@pytest.mark.asyncio
async def test_get_memory_detects_conflicts(json_backend, config):
    """get_memory annotates conflicting key-value pairs."""
    # Save 2 entries with different budgets via different namespaces (no dedup)
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="alice",
        namespace="seo",
    )
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=40K", reason="bob",
        namespace="ads",
    )
    result = await T.get_memory(json_backend, client_id="_default")
    assert "CONFLICT" in result
    assert "budget" in result
    assert "50K" in result
    assert "40K" in result


@pytest.mark.asyncio
async def test_search_memory_detects_conflicts(json_backend, config):
    """search_memory annotates conflicting key-value pairs."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="alice",
        namespace="seo",
    )
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=40K", reason="bob",
        namespace="ads",
    )
    result = await T.search_memory(json_backend, None, query="budget")
    assert "CONFLICT" in result
    assert "budget" in result


@pytest.mark.asyncio
async def test_no_conflict_when_same_values(json_backend, config):
    """No CONFLICT annotation when all values for a key agree."""
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="confirmed",
        namespace="seo",
    )
    await T.save_memory(
        json_backend, config, None,
        category="business_context", type="insight",
        content="budget=50K", reason="confirmed again",
        namespace="ads",
    )
    result = await T.get_memory(json_backend, client_id="_default")
    assert "CONFLICT" not in result
