# Architecture

## Overview

Pimemento follows a three-layer architecture:

```
Transport Layer          Business Logic          Storage Layer
─────────────           ──────────────          ─────────────
server.py           →    tools.py           →    MemoryBackend (ABC)
embedded.py              (5 functions)            ├── JsonBackend
(FastMCP wrappers)       parse_kv()               └── PostgresBackend
                         normalize_category()
                         save/get/delete/          Embedder (ABC)
                         status/search             ├── LocalEmbedder
                                                   └── OpenAIEmbedder
```

## Data Flow

### save_memory

```
1. Transport layer receives MCP tool call
2. tools.save_memory() validates inputs (category, type, content, reason)
3. Builds MemoryEntry dataclass
4. If embedder available: entry.embedding = embed(content)
5. Calls backend.find_duplicates(entry, threshold)
   - JSON: key-based overlap on (client_id, namespace, user_id, category)
   - Postgres: cosine similarity on (client_id, namespace, user_id)
6. If duplicate found:
   - Detect changed values (old vs new for shared keys)
   - Merge content (union of kv pairs, new values win)
   - Update metadata, type, category, reason, timestamp
   - backend.save(existing)  → UPDATE
   - Return change details: "Updated (keys: budget | changed: budget 50K->40K (was @alice 2025-02-20))"
7. Else:
   - backend.save(entry)     → INSERT
8. Return formatted string response
```

### search_memory

```
1. Transport layer receives MCP tool call
2. tools.search_memory() validates query
3. If embedder available: query_embedding = embed(query)
4. Calls backend.search(query, ..., query_embedding=embedding)
   - Postgres with embeddings: pgvector cosine similarity (ORDER BY embedding <=> query)
   - Postgres without embeddings: full-text search (ts_rank on content_tsv), ILIKE fallback
   - JSON: substring match on content + reason + category
5. Format results with similarity scores
6. Detect key-value conflicts across results, append CONFLICT annotations
```

## Backend Abstraction

All backend methods are async. The JSON backend wraps sync file I/O in `asyncio.to_thread()` to maintain a uniform interface.

```python
class MemoryBackend(ABC):
    async def save(entry) -> MemoryEntry
    async def get(client_id, ...) -> list[MemoryEntry]
    async def delete(client_id, content_match, ...) -> MemoryEntry | None
    async def status(client_id, ...) -> dict
    async def search(query, client_id, ...) -> list[(MemoryEntry, float)]
    async def find_duplicates(entry, threshold) -> list[(MemoryEntry, float)]
    async def close() -> None
```

## Deployment Modes

### Standalone (recommended for multi-MCP)

```
MCP Sales ────┐
MCP Support ──┤  Each MCP has 0 memory tools.
MCP Analytics─┤  Pimemento runs as a separate MCP server.
              │
         Pimemento (5 tools, shared memory)
```

### Embedded (simpler setup)

```
My MCP Server
├── Domain tools (run_tests, deploy, ...)
└── Pimemento tools (save_memory, get_memory, ...)
    └── register_tools(mcp)
```

## Key Design Decisions

1. **All async**: Uniform interface regardless of backend. JSON backend uses `asyncio.to_thread()`.
2. **Lazy imports**: Heavy deps (torch, asyncpg) only imported inside concrete classes. JSON mode starts in <2s.
3. **find_duplicates separate from save**: Backend provides similarity data, tools.py decides merge logic.
4. **Embeddings computed in tools.py**: Backend stores, doesn't compute. Separation of concerns.
5. **Embedded = closure state**: No module globals. Multiple MCP servers can embed Pimemento independently.
6. **Postgres dedup not scoped to category**: Catches cross-category semantic duplicates.
