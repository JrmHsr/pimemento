# Comparisons

## Pimemento vs server-memory (official MCP)

**server-memory** uses a knowledge graph (entities, relations, observations). It's powerful for reasoning about relationships.

**Pimemento** uses structured key-value pairs with optional semantic vectors. It's optimized for lightweight context persistence.

| Aspect | server-memory | Pimemento |
|--------|:---:|:---:|
| Model | Knowledge graph | Key-value + JSONB |
| Tools | 9 (~1500 tokens) | 5 (~750 tokens) |
| Read cost | Full graph returned | Paginated, filtered |
| Multi-tenant | No | Yes (client_id) |
| Multi-user | No | Yes (user_id) |
| Cross-MCP | No | Yes (namespace) |
| Dedup | By entity name | Key + cosine similarity |
| TTL | No | Per-entry expiration |
| Search | Entity-based | Semantic (pgvector) |

**When to use server-memory**: You need entity relationships ("John works at Acme, Acme is in healthcare").

**When to use Pimemento**: You need fast, cheap context that survives across sessions with minimal token cost.

## Pimemento vs mcp-memory-postgres

**mcp-memory-postgres** focuses on Postgres storage with semantic search.

| Aspect | mcp-memory-postgres | Pimemento |
|--------|:---:|:---:|
| Backend | Postgres only | JSON + Postgres |
| Multi-tenant | No | Yes |
| Multi-user | No | Yes |
| Cross-MCP | No | Yes |
| Schema-less | No | Yes (JSONB metadata) |
| Zero-dep mode | No | Yes (JSON backend) |
| Embedded mode | No | Yes |

