#!/usr/bin/env python3
"""Pimemento - Standalone MCP memory server.

Shared memory layer for AI teams. Multi-tenant. Cross-MCP. Schema-less.

Usage:
    pimemento                                           # stdio (default)
    pimemento --transport streamable-http --port 8770   # HTTP
    python -m pimemento.server                          # module mode

Environment:
    MEMORY_BACKEND          json (default) | postgres
    MEMORY_DIR              Base directory for JSON files (default: ./memory_data)
    DATABASE_URL            Postgres connection string
    EMBEDDING_PROVIDER      none | local | openai
    MEMORY_PORT             HTTP port (default: 8770)
    See config.py for full list.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server.fastmcp import FastMCP

from pimemento import tools as T
from pimemento.backends import get_backend
from pimemento.backends.base import MemoryBackend
from pimemento.config import PimementoConfig
from pimemento.embeddings import get_embedder
from pimemento.embeddings.base import Embedder

logger = logging.getLogger(__name__)

# ── Globals initialized at startup ──
_backend: MemoryBackend | None = None
_embedder: Embedder | None = None
_config: PimementoConfig | None = None

mcp = FastMCP(
    "Pimemento",
    instructions=(
        "Shared AI memory layer for teams. "
        "Call get_memory at session start. Call save_memory when context is shared "
        "(decisions, preferences, constraints, business context). "
        "Format: key=value | key=value (max 500 chars). "
        "search_memory for semantic retrieval. memory_status for metadata."
    ),
)


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
      "persona=seniors | monetization=affiliate"
      "allergy=penicillin | blood_type=O+"
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
    if _backend is None or _config is None:
        raise RuntimeError("Pimemento not initialized")

    meta, err = T.parse_metadata(metadata)
    if err:
        return err

    return await T.save_memory(
        _backend,
        _config,
        _embedder,
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
    if _backend is None:
        raise RuntimeError("Pimemento not initialized")
    return await T.get_memory(
        _backend,
        client_id=client_id,
        user_id=user_id,
        namespace=namespace,
        category=category,
        type=type,
        limit=limit,
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
    if _backend is None:
        raise RuntimeError("Pimemento not initialized")
    return await T.delete_memory(
        _backend,
        content_match=content_match,
        client_id=client_id,
        user_id=user_id,
        namespace=namespace,
        category=category,
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
    if _backend is None:
        raise RuntimeError("Pimemento not initialized")
    return await T.memory_status(
        _backend,
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
    if _backend is None:
        raise RuntimeError("Pimemento not initialized")
    return await T.search_memory(
        _backend,
        _embedder,
        query=query,
        client_id=client_id,
        user_id=user_id,
        namespace=namespace,
        limit=limit,
    )


async def _initialize() -> None:
    """Initialize backend and embedder from config."""
    global _backend, _embedder, _config
    _config = PimementoConfig.from_env()
    _embedder = get_embedder(_config)
    _backend = await get_backend(_config)


class _BearerAuthMiddleware:
    """ASGI middleware that enforces Bearer token authentication."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if not auth.startswith("Bearer ") or auth[7:] != self.token:
                from starlette.responses import Response

                resp = Response("Unauthorized", status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main() -> None:
    """CLI entry point."""
    transport = "stdio"
    port: int | None = None
    host: str | None = None

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--transport" and i + 1 < len(args):
            transport = args[i + 1]
        elif arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif arg == "--host" and i + 1 < len(args):
            host = args[i + 1]

    # Initialize backend before running server
    asyncio.run(_initialize())

    if _config is None:
        raise RuntimeError("Pimemento config not loaded")
    if transport == "streamable-http":
        effective_host = host or _config.memory_host
        effective_port = port or _config.memory_port

        if _config.auth_token:
            # Wrap with Bearer auth middleware and serve via uvicorn
            import uvicorn

            app = mcp.streamable_http_app()
            app = _BearerAuthMiddleware(app, _config.auth_token)
            uvicorn.run(app, host=effective_host, port=effective_port)
        else:
            logger.warning(
                "MEMORY_AUTH_TOKEN not set — HTTP transport is unauthenticated. "
                "Set MEMORY_AUTH_TOKEN env var to enable Bearer token auth."
            )
            mcp.run(
                transport="streamable-http",
                host=effective_host,
                port=effective_port,
            )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
