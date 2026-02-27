# Triggering Memory Calls

> System prompt instructions alone do NOT reliably trigger memory calls.
> The most reliable triggers are **tool responses** from domain MCP servers.

## Strategy A: Dedicated Memory MCP (recommended)

When Pimemento runs as a separate MCP server, the system prompt tells the LLM when to call memory tools. Domain MCPs don't need any memory logic.

**System prompt instruction:**
```
At session start: call get_memory() to load user context.
After any state-changing interaction: if the user shared context
(decision, preference, constraint), call save_memory().
```

## Strategy B: Embedded in domain MCP

When memory tools are embedded in a domain MCP via `register_tools()`, inject reminders in tool responses as a backup trigger:

**Entry-point tools** (session start, status checks):
```
LOAD CONTEXT: get_memory('{client_id}')
```

**State-changing tools** (save, update, create):
```
MEMORY: context shared? → save_memory() | correction? → delete_memory()
```

Keep trigger text under 25 tokens to minimize overhead.

## What to Persist

| Persist | Don't persist |
|---------|--------------|
| User decisions and preferences | Tool outputs or raw data |
| Business constraints and context | Intermediate calculations |
| Explicit corrections/reversals | Session-specific state |
| What changes future behavior | What can be re-derived |

## Examples

**Good saves:**
```
save_memory(content="persona=seniors | budget=15K", type="insight")
save_memory(content="never_call_client=true", type="exclusion")
save_memory(content="stack=React | deploy=Vercel", type="decision")
```

**Don't save:**
```
# Tool output (re-derivable)
save_memory(content="page_has_234_words")  # ❌

# Session state
save_memory(content="currently_editing_homepage")  # ❌

# Raw data
save_memory(content="full_crawl_results_json")  # ❌
```
