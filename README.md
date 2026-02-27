# Pimemento

**Shared memory layer for AI agents. Multi-tenant. Cross-MCP. Schema-less.**

🌐 [www.pimemento.ai](https://www.pimemento.ai)

---

## The Problem

LLMs forget everything between sessions. Every conversation starts from zero.

When a team uses multiple MCP servers or AI agents, context doesn't flow between them. Alice (engineering) documents a blocker during a sprint — Bob (product) never sees it. The wiki has a 30% adoption rate because it's too much friction.

Existing memory solutions are:
- **Mono-user** — no team sharing
- **Schema-fixed** — you have to model your data upfront
- **Token-heavy** — 9 tools, 1500+ schema tokens

## The Solution

Pimemento is a shared memory layer that plugs into any MCP server. It gives AI agents persistent, cross-session, cross-MCP memory with:

- **5 tools, ~750 schema tokens** — lightweight by design
- **Schema-less** — `metadata JSONB` adapts to any domain without migration
- **Multi-tenant** — `client_id` isolates projects/clients
- **Multi-user** — `user_id` tracks who wrote what (Alice notes, Bob reads)
- **Cross-MCP** — `namespace` connects context across domain servers
- **Semantic dedup** — no duplicates even when people phrase things differently
- **Semantic search** — find memories by meaning, not just keywords
- **Conflict detection** — flags contradictory values across entries and on merge
- **Dual backend** — JSON (zero deps) for dev, Postgres + pgvector for production

## Comparison

|                        | server-memory (official) | mcp-memory-postgres | **Pimemento** |
|------------------------|:---:|:---:|:---:|
| Tools                  | 9 (~1500 tok) | 6+ | **5 (~750 tok)** |
| Multi-tenant           | No | No | **Yes** |
| Multi-user             | No | No | **Yes** |
| Cross-MCP              | No | No | **Yes** |
| Schema-less            | No | No | **Yes (JSONB)** |
| Semantic dedup         | No | No | **Key + vector** |
| Semantic search        | No | Yes | **Yes** |
| Backend                | JSON | Postgres | **JSON + Postgres** |
| Designed for           | Dev | Dev | **Teams / Production** |

## Quick Start — JSON mode (30 seconds)

```bash
pip install pimemento
pimemento
```

That's it. The server starts on stdio. Add it to your MCP client config:

```json
{
  "mcpServers": {
    "pimemento": {
      "command": "pimemento"
    }
  }
}
```

## Quick Start — Postgres mode (2 minutes)

```bash
# Start Postgres with pgvector
docker-compose up -d

# Install with Postgres dependencies
pip install "pimemento[postgres,embeddings-local]"

# Run with Postgres backend
export MEMORY_BACKEND=postgres
export DATABASE_URL=postgresql://pimemento:pimemento@localhost:5432/pimemento
pimemento
```

## Tools

### `save_memory`

Persist context for future sessions. Auto-deduplicates by key + semantic similarity.

```
save_memory(
    category="project_config",
    type="insight",
    content="stack=React+Node | deploy=Vercel | blocker=auth_migration",
    reason="Team shared current stack and blockers",
    client_id="acme_project",
    user_id="alice",
    namespace="engineering"
)
→ "Saved.\nproject_config | insight"
```

### `get_memory`

Load accumulated context. **Call at session start.**

```
get_memory(client_id="acme_project")
→ Memory 'acme_project' (3):
  2025-02-26 INSIGHT | engineering/project_config | stack=React+Node | deploy=Vercel @alice
  2025-02-25 DECISION | product/domain_context | priority=auth_revamp | deadline=Q2 @bob
  2025-02-24 INSIGHT | support/user_preference | escalation=slack_channel @carol
```

### `delete_memory`

Remove a memory when the user reverses or corrects a decision.

```
delete_memory(content_match="blocker", client_id="acme_project")
→ "Deleted: stack=React+Node | deploy=Vercel | blocker=auth_migration"
```

### `memory_status`

Lightweight metadata — no content loaded, ~20 tokens.

```
memory_status(client_id="acme_project", namespace="engineering")
→ "'acme_project': 7 entries | ns: engineering | cat: project_config, domain_context | 2025-01-15 -> 2025-02-26"
```

### `search_memory`

Search by meaning (pgvector cosine), full-text search (ts_rank), or keyword (substring fallback on JSON). Automatically detects conflicting key-value pairs across results.

```
search_memory(query="what tech stack are we using", client_id="acme_project")
→ Search 'what tech stack are we using' (2 results):
  [0.94] 2025-02-26 INSIGHT | engineering/project_config | stack=React+Node | deploy=Vercel @alice
  [0.87] 2025-02-20 DECISION | engineering/project_config | CI=GitHub_Actions | staging=preview_deploys @alice
```

When conflicting values are detected, a `CONFLICT` annotation is appended:

```
get_memory(client_id="acme_project")
→ Memory 'acme_project' (2):
  2025-02-26 INSIGHT | sales/business_context | budget=40K @bob
  2025-02-20 INSIGHT | sales/business_context | budget=50K @alice
  ---
  CONFLICT budget: current=40K (2025-02-26), previous=50K (2025-02-20 @alice)
```

## Architecture

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ MCP Sales   │   │MCP Support  │   │MCP Analytics│
│  0 memory   │   │  0 memory   │   │  0 memory   │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                 │
       └────────┬────────┴────────┬────────┘
                │                 │
       ┌────────┴─────────┐      │
       │    Pimemento     │◄─────┘
       │    5 tools       │
       │    shared memory │
       │    ~750 tok      │
       │                  │
       │  ┌────────────┐  │
       │  │ JSON or    │  │
       │  │ Postgres + │  │
       │  │ pgvector   │  │
       │  └────────────┘  │
       └──────────────────┘
```

## Team Workflow

```
Alice (eng)      →  save_memory(user_id="alice", content="stack=React+Node | deploy=Vercel")
Bob (product)    →  get_memory(client_id="acme_project")    # sees Alice's notes
Carol (support)  →  search_memory(query="deploy")            # finds everything about deploys
```

Each person uses their own `user_id`. The shared `client_id` connects them. Everyone sees the whole team's context unless they filter by `user_id`.

## Embedded Mode

Don't want a separate MCP server? Embed Pimemento's 5 tools directly into your existing MCP:

```python
from mcp.server.fastmcp import FastMCP
from pimemento import register_tools

mcp = FastMCP("My Dev Tools")
register_tools(mcp)  # adds 5 memory tools

@mcp.tool()
def run_tests(path: str) -> str:
    return f"Running tests in {path}"

mcp.run()
```

## Configuration

All via environment variables (`.env` supported):

```bash
# Backend
MEMORY_BACKEND=json                # "json" (default) | "postgres"

# JSON backend
MEMORY_DIR=./memory_data           # Storage directory
MAX_ENTRIES_PER_CLIENT=0           # Max entries per client (0 = unlimited)
MAX_CONTENT_LEN=500                # Max chars per content field

# Postgres backend
DATABASE_URL=postgresql://user:pass@localhost:5432/memory_db

# Embeddings (Postgres only)
EMBEDDING_PROVIDER=local           # "local" | "openai" | "none"
EMBEDDING_MODEL=all-MiniLM-L6-v2  # sentence-transformers model
OPENAI_API_KEY=sk-...              # If provider=openai
EMBEDDING_DIMENSIONS=384           # Must match the model

# Semantic dedup
SEMANTIC_DEDUP_THRESHOLD=0.85      # Cosine similarity to trigger merge

# Server
MEMORY_HOST=0.0.0.0
MEMORY_PORT=8770
```

## Content Format

```
key=value | key=value | key=value
```

**Why key=value, not JSON?**
- 2-3x fewer tokens (`{"key":"value"}` = 7 tokens, `key=value` = 3)
- Enables key-based dedup without NLP
- LLMs naturally produce and parse this format

**Key=value is recommended, not enforced.** The server warns if no `=` is detected but still saves the entry.

**Examples across domains:**

```
# Software Engineering
stack=React+Node | deploy=Vercel | CI=GitHub_Actions

# Sales / CRM
client=Acme | deal_size=50K | next_step=demo_thursday

# Healthcare
patient=anonymized | allergy=penicillin | blood_type=O+

# Legal
jurisdiction=FR | entity=SAS | fiscal_year=calendar
```

## Categories

| Category | What it stores |
|----------|---------------|
| `business_context` | Company, market, monetization, personas |
| `project_config` | Stack, architecture, conventions |
| `user_preference` | Workflow, tone, formatting |
| `domain_context` | Domain-specific decisions |
| `analysis_context` | Recurring findings, baselines |
| `content_strategy` | Editorial guidelines, funnel rules |
| Custom: `x_*` | Your domain (e.g. `x_medical_history`) |

## Types

| Type | When to use |
|------|-------------|
| `decision` | User chose between options |
| `exclusion` | User explicitly rejected something |
| `insight` | Factual context |
| `action` | User committed to a plan |
| `anomaly` | Unexpected finding worth remembering |

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

```bash
git clone https://github.com/JrmHsr/pimemento
cd pimemento
pip install -e ".[dev]"
pytest
```

## Author

Built by [Jérémy Husser](https://github.com/JrmHsr)

## License

MIT
