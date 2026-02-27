"""Embedded mode: register Pimemento tools on an existing FastMCP server.

Usage:
    from mcp.server.fastmcp import FastMCP
    from pimemento import register_tools

    mcp = FastMCP("My App")
    register_tools(mcp)
    mcp.run()
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from pimemento import tools as T
from pimemento.backends.base import MemoryBackend
from pimemento.config import PimementoConfig
from pimemento.embeddings.base import Embedder


def register_tools(
    mcp: Any,
    *,
    config: PimementoConfig | None = None,
    backend: MemoryBackend | None = None,
    embedder: Embedder | None = None,
    category_aliases: dict[str, str] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Register Pimemento's 5 memory tools on an existing FastMCP server.

    Three modes:
    1. No args: auto-loads config from env, creates backend/embedder on first call.
    2. Config only: creates backend/embedder from provided config.
    3. Backend + embedder provided: uses them directly (useful for testing).

    Args:
        mcp: FastMCP server instance.
        config: Optional config (default: loaded from env vars).
        backend: Optional pre-initialized backend.
        embedder: Optional pre-initialized embedder.
        category_aliases: Optional dict mapping legacy category names to standard ones.

    Returns:
        Dict of tool name -> async callable for direct invocation.
    """
    # Closure-captured state (not globals, for multi-MCP isolation)
    _state: dict[str, Any] = {
        "backend": backend,
        "embedder": embedder,
        "config": config,
        "initialized": backend is not None,
        "lock": asyncio.Lock(),
        "category_aliases": dict(category_aliases) if category_aliases else {},
    }

    async def _ensure_init() -> None:
        if _state["initialized"]:
            return
        async with _state["lock"]:
            if _state["initialized"]:
                return
            cfg = _state["config"] or PimementoConfig.from_env()
            _state["config"] = cfg
            if _state["embedder"] is None and cfg.embeddings_enabled:
                from pimemento.embeddings import get_embedder

                _state["embedder"] = get_embedder(cfg)
            if _state["backend"] is None:
                from pimemento.backends import get_backend

                _state["backend"] = await get_backend(cfg)
            _state["initialized"] = True

    @mcp.tool()
    async def save_memory(
        category: str,
        type: str,
        content: str,
        reason: str,
        client_id: str = "_default",
        user_id: str = "_anonymous",
        namespace: str = "general",
        source_mcp: str = "",
        ttl_days: int = 0,
        metadata: str = "",
    ) -> str:
        """Persist context for future sessions.

        FORMAT: key=value pairs separated by ' | ':
          "stack=React+Node | deploy=Vercel | CI=GitHub_Actions"
        Max 500 chars. Auto-deduplicates by key + semantic similarity.

        Args:
            category: business_context, project_config, user_preference,
                      domain_context, analysis_context, content_strategy (or x_ custom).
            type: decision | exclusion | anomaly | insight | action.
            content: Structured key=value data (max 500 chars, no prose).
            reason: Why persist this (1 short sentence).
            client_id: User or project identifier (default: '_default').
            user_id: Human user identifier (default: '_anonymous').
            namespace: Source MCP or domain (default: 'general').
            source_mcp: Name of the originating MCP server.
            ttl_days: Auto-expire after N days (0 = permanent).
            metadata: Optional JSON string of additional metadata.
        """
        await _ensure_init()

        meta, err = T.parse_metadata(metadata)
        if err:
            return err

        return await T.save_memory(
            _state["backend"],
            _state["config"],
            _state["embedder"],
            category=category,
            type=type,
            content=content,
            reason=reason,
            client_id=client_id,
            user_id=user_id,
            namespace=namespace,
            source_mcp=source_mcp,
            ttl_days=ttl_days,
            metadata=meta,
            category_aliases=_state["category_aliases"] or None,
        )

    @mcp.tool()
    async def get_memory(
        client_id: str = "_default",
        user_id: str = "",
        namespace: str = "",
        category: str = "",
        type: str = "",
        limit: int = 20,
    ) -> str:
        """Load accumulated context. Call at session start.

        Args:
            client_id: User or project identifier (default: '_default').
            user_id: Filter by human user. Empty = all users.
            namespace: Filter by source MCP. Empty = all.
            category: Filter by category. Empty = all.
            type: Filter by type. Empty = all.
            limit: Max entries returned (default: 20).
        """
        await _ensure_init()
        return await T.get_memory(
            _state["backend"],
            client_id=client_id,
            user_id=user_id,
            namespace=namespace,
            category=category,
            type=type,
            limit=limit,
            category_aliases=_state["category_aliases"] or None,
        )

    @mcp.tool()
    async def delete_memory(
        content_match: str,
        client_id: str = "_default",
        user_id: str = "",
        namespace: str = "",
        category: str = "",
    ) -> str:
        """Remove a memory entry when the user reverses or corrects a decision.

        Matches by key or substring in content. Deletes most recent match.

        Args:
            content_match: Key or substring to search (e.g. "persona", "allergy").
            client_id: User or project identifier.
            user_id: Filter by human user. Empty = any user.
            namespace: Optional filter by source MCP.
            category: Optional filter by category.
        """
        await _ensure_init()
        return await T.delete_memory(
            _state["backend"],
            content_match=content_match,
            client_id=client_id,
            user_id=user_id,
            namespace=namespace,
            category=category,
            category_aliases=_state["category_aliases"] or None,
        )

    @mcp.tool()
    async def memory_status(
        client_id: str = "_default",
        user_id: str = "",
        namespace: str = "",
    ) -> str:
        """Lightweight metadata -- no content loaded, minimal tokens (~20).

        Returns entry count, namespaces, categories, and date range.
        Use before get_memory to decide what to load.

        Args:
            client_id: User or project identifier (default: '_default').
            user_id: Filter by human user. Empty = all users.
            namespace: Filter by source MCP. Empty = all.
        """
        await _ensure_init()
        return await T.memory_status(
            _state["backend"],
            client_id=client_id,
            user_id=user_id,
            namespace=namespace,
        )

    @mcp.tool()
    async def search_memory(
        query: str,
        client_id: str = "_default",
        user_id: str = "",
        namespace: str = "",
        limit: int = 10,
    ) -> str:
        """Search memory by meaning (semantic) or keyword (substring fallback).

        Uses pgvector cosine similarity when available (Postgres backend),
        falls back to substring matching on JSON backend.

        Args:
            query: Natural language query or keywords.
            client_id: User or project identifier (default: '_default').
            user_id: Filter by human user. Empty = all users.
            namespace: Filter by source MCP. Empty = all.
            limit: Max results (default: 10).
        """
        await _ensure_init()
        return await T.search_memory(
            _state["backend"],
            _state["embedder"],
            query=query,
            client_id=client_id,
            user_id=user_id,
            namespace=namespace,
            limit=limit,
        )

    return {
        "save_memory": save_memory,
        "get_memory": get_memory,
        "delete_memory": delete_memory,
        "memory_status": memory_status,
        "search_memory": search_memory,
    }
