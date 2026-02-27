"""Embed Pimemento memory tools into an existing MCP server.

This adds 5 memory tools to your MCP server without running a separate process.
Your domain tools + memory tools share the same server.

Usage:
    python examples/embedded_in_mcp.py
"""

from mcp.server.fastmcp import FastMCP

from pimemento import register_tools

# Your existing MCP server
mcp = FastMCP(
    "My Dev Tools",
    instructions="Development tools with built-in memory.",
)


# Register Pimemento's 5 memory tools
tools = register_tools(mcp)


# Your domain tools
@mcp.tool()
def run_tests(path: str) -> str:
    """Run tests for a given path."""
    # Your domain logic here...
    return f"Tests in {path}: 42 passed, 0 failed."


if __name__ == "__main__":
    mcp.run(transport="stdio")
