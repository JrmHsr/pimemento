"""Quickstart: run Pimemento as a standalone MCP server.

1. pip install pimemento
2. python examples/standalone_quickstart.py

The server starts on stdio by default. Connect to it from any MCP client
(Claude Desktop, Cursor, etc.) by adding to your MCP config:

{
    "mcpServers": {
        "pimemento": {
            "command": "pimemento"
        }
    }
}

Or for HTTP transport:

    pimemento --transport streamable-http --port 8770
"""

from pimemento.server import main

if __name__ == "__main__":
    main()
