# Lore Context MCP Server

A [FastMCP](https://github.com/PrefectHQ/fastmcp) server that wraps the
[Lore Context](https://github.com/nousresearch/lore) REST API, exposing
governed agent memory as MCP tools.

Any MCP-compatible client (Claude Desktop, Cursor, Codex, etc.) can search,
write, read, list, and forget memories through this server.

## Prerequisites

- Python ≥ 3.10
- A running [Lore Context API](http://127.0.0.1:3000) instance
- An API key with at least `reader` role (write/forget require `writer`/`admin`)

## Quick Start

```bash
# Install dependencies
pip install fastmcp httpx

# Set environment variables
export LORE_API_URL=http://127.0.0.1:3000   # your Lore API URL
export LORE_API_KEY=your-api-key-here

# Run the server (stdio transport, default for MCP)
python server.py

# Or use the FastMCP CLI
fastmcp run server.py
```

## Tools

| Tool            | Mutates | Description                                    |
|-----------------|---------|------------------------------------------------|
| `memory_search` | no      | Semantic search over Lore memories             |
| `memory_write`  | yes     | Write a new governed memory                    |
| `memory_get`    | no      | Fetch a single memory by id                    |
| `memory_list`   | no      | List memories with optional filters            |
| `memory_forget` | yes     | Soft-delete or hard-delete memories            |

## Configuration

Set these environment variables before running:

| Variable       | Default                  | Description                      |
|----------------|--------------------------|----------------------------------|
| `LORE_API_URL` | `http://127.0.0.1:3000`  | Base URL of the Lore Context API |
| `LORE_API_KEY` | *(none – required)*      | Bearer token for authentication  |

## Using with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lore-context": {
      "command": "python",
      "args": ["/path/to/examples/lore_context_server/server.py"],
      "env": {
        "LORE_API_URL": "http://127.0.0.1:3000",
        "LORE_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Using with the FastMCP CLI

```bash
# Inspect the server
fastmcp inspect server.py

# Run with streamable HTTP transport
fastmcp run server.py --transport streamable-http --port 8000
```

## Example Usage (Python Client)

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("server.py") as client:
        # List available tools
        tools = await client.list_tools()
        print([t.name for t in tools])

        # Search memories
        results = await client.call_tool("memory_search", {
            "query": "FastMCP server architecture",
            "top_k": 5,
        })
        print(results)

        # Write a memory
        result = await client.call_tool("memory_write", {
            "content": "The Lore Context server uses httpx for REST calls.",
            "scope": "project",
            "project_id": "fastmcp-demo",
        })
        print(result)

asyncio.run(main())
```

## Lore Context REST API

This server proxies these Lore Context REST endpoints:

| MCP Tool         | HTTP Method | REST Endpoint         |
|------------------|-------------|-----------------------|
| `memory_search`  | POST        | `/v1/memory/search`   |
| `memory_write`   | POST        | `/v1/memory/write`    |
| `memory_get`     | GET         | `/v1/memory/{id}`     |
| `memory_list`    | GET         | `/v1/memory/list`     |
| `memory_forget`  | POST        | `/v1/memory/forget`   |

For the full API reference, see the
[Lore Context API docs](https://github.com/nousresearch/lore/blob/main/docs/api-reference.md).
