"""Multi-MCP architecture: N domain MCPs + 1 shared Pimemento.

In this pattern, domain MCPs handle their specialty (Sales, Support, Analytics).
Pimemento runs as a dedicated MCP that holds shared context.

Architecture:
    MCP Sales     ---> 0 memory tools, does sales
    MCP Support   ---> 0 memory tools, does support
    MCP Analytics ---> 0 memory tools, does analytics
    Pimemento     ---> 5 tools, shared memory, ~750 schema tokens

MCP client config (e.g. Claude Desktop):
{
    "mcpServers": {
        "sales": {
            "command": "python",
            "args": ["sales_server.py"]
        },
        "support": {
            "command": "python",
            "args": ["support_server.py"]
        },
        "pimemento": {
            "command": "pimemento"
        }
    }
}

The LLM uses namespace to track which domain the context came from:
    save_memory(namespace="sales", content="client=Acme | deal_size=50K")
    save_memory(namespace="support", content="ticket=1234 | priority=high")
    get_memory()  # returns ALL context across domains
"""

# Sales MCP (no memory tools)
from mcp.server.fastmcp import FastMCP

sales_mcp = FastMCP(
    "Sales Tools",
    instructions=(
        "Sales pipeline tools. "
        "IMPORTANT: use Pimemento (separate MCP) for memory. "
        "After a call, suggest save_memory(namespace='sales', ...) "
        "to persist key findings."
    ),
)


@sales_mcp.tool()
def check_pipeline(stage: str) -> str:
    """Check deals at a given pipeline stage."""
    # Your sales logic here...
    return f"Pipeline stage '{stage}': 12 deals, $340K total."


if __name__ == "__main__":
    # Run only the Sales server. Pimemento runs separately.
    sales_mcp.run(transport="stdio")
