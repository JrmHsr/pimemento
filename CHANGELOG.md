# Changelog

## 1.0.0 (2025-02-27)

First stable release. Shared memory layer for AI agents — multi-tenant, cross-MCP, schema-less.

### Highlights

- **5 tools, ~750 schema tokens** — `save_memory`, `get_memory`, `delete_memory`, `search_memory`, `memory_status`
- **Dual backend** — JSON (zero deps) for dev, Postgres + pgvector for production
- **Multi-tenant** — `client_id` isolates projects/clients
- **Multi-user** — `user_id` tracks who wrote what
- **Cross-MCP** — `namespace` connects context across domain servers
- **Semantic dedup** — key-based + vector cosine similarity (Postgres)
- **Semantic search** — pgvector cosine or substring fallback (JSON)
- **Schema-less** — `metadata JSONB` adapts to any domain
- **Embedded mode** — `register_tools(mcp)` adds memory to any existing MCP server
- **Standalone mode** — `pimemento` CLI starts a stdio MCP server
- **Docker ready** — docker-compose.yml with Postgres 16 + pgvector
