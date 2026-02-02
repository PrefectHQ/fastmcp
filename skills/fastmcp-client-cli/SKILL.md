---
name: fastmcp-client-cli
description: Query and invoke tools on MCP servers using fastmcp list and fastmcp call. Use when you need to discover what tools a server offers, call tools, or integrate MCP servers into workflows.
---

# FastMCP CLI: List and Call

Use `fastmcp list` and `fastmcp call` to interact with any MCP server from the command line.

## Listing Tools

```bash
# Remote server
fastmcp list http://localhost:8000/mcp

# Local Python file (runs via fastmcp run automatically)
fastmcp list server.py

# MCPConfig with multiple servers
fastmcp list mcp.json

# Stdio command (npx, uvx, etc.)
fastmcp list --command 'npx -y @modelcontextprotocol/server-github'

# Include full input/output schemas
fastmcp list server.py --input-schema --output-schema

# Machine-readable JSON
fastmcp list server.py --json

# Include resources and prompts
fastmcp list server.py --resources --prompts
```

Default output shows tool signatures and descriptions. Use `--input-schema` or `--output-schema` to include full JSON schemas, `--json` for structured output.

## Calling Tools

```bash
# Key=value arguments (auto-coerced to correct types)
fastmcp call server.py greet name=World
fastmcp call server.py add a=3 b=4

# Single JSON object for complex/nested args
fastmcp call server.py create_item '{"name": "Widget", "tags": ["a", "b"]}'

# --input-json with key=value overrides
fastmcp call server.py search --input-json '{"query": "hello", "limit": 5}' limit=10

# JSON output for scripting
fastmcp call server.py add a=3 b=4 --json
```

Type coercion is automatic: `limit=5` becomes an integer, `verbose=true` becomes a boolean, based on the tool's input schema.

## Server Targets

All commands accept the same server targets:

| Target | Example |
|--------|---------|
| HTTP/HTTPS URL | `http://localhost:8000/mcp` |
| Python file | `server.py` |
| MCPConfig JSON | `mcp.json` (must have `mcpServers` key) |
| Stdio command | `--command 'npx -y @mcp/server'` |
| Discovered name | `weather` or `source:name` |

Servers configured in editor configs (Claude Desktop, Claude Code, Cursor, Gemini CLI, Goose) or project-level `mcp.json` can be referenced by name. Use `source:name` (e.g. `claude-code:my-server`, `cursor:weather`) to target a specific source. Run `fastmcp discover` to see available names.

For SSE servers, pass `--transport sse`:

```bash
fastmcp list http://localhost:8000/mcp --transport sse
```

## Auth

HTTP targets automatically use OAuth (no-ops if the server doesn't require auth). Disable with `--auth none`:

```bash
fastmcp call http://server/mcp tool --auth none
```

## Discovering Configured Servers

```bash
# See all MCP servers in editor/project configs
fastmcp discover

# Filter by source
fastmcp discover --source claude-code

# JSON output
fastmcp discover --json
```

Scans Claude Desktop, Claude Code, Cursor, Gemini CLI, Goose, and `./mcp.json`. Sources: `claude-desktop`, `claude-code`, `cursor`, `gemini`, `goose`, `project`.

## Workflow Pattern

Discover tools first, then call them:

```bash
# 1. See what servers are configured
fastmcp discover

# 2. See what tools a server has
fastmcp list weather

# 3. Call a tool
fastmcp call weather get_forecast city=London
```

If you call a nonexistent tool, FastMCP suggests close matches.
