# FastMCP Tasks Example

Demonstrates background task execution with Docket, including progress tracking, distributed backends, and CLI worker management.

## Setup

```bash
# From the fastmcp root directory
uv sync

# Start Redis
cd examples/tasks
docker compose up -d

# Load environment (or source .envrc manually)
direnv allow

# Run the server
fastmcp run server.py
```

For single-process mode without Redis, set `FASTMCP_DOCKET_URL=memory://` (note: CLI workers won't work).

## Running the Client

```bash
# Background execution with progress callbacks
python examples/tasks/client.py --duration 10

# Immediate execution (blocks)
python examples/tasks/client.py immediate --duration 5
```

## Starting Additional Workers

With Redis, you can run additional workers to process tasks in parallel:

```bash
fastmcp tasks worker server.py

# Configure via environment:
export FASTMCP_DOCKET_CONCURRENCY=20
fastmcp tasks worker server.py
```

**Backend options:**
- `memory://` - Single-process only (default)
- `redis://` - Distributed, multi-process (Redis or Valkey)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTMCP_DOCKET_URL` | `memory://` | Docket backend URL |

## Learn More

- [FastMCP Tasks Documentation](https://gofastmcp.com/docs/tasks)
- [Docket Documentation](https://github.com/PrefectHQ/docket)
- [MCP Task Protocol (SEP-1686)](https://spec.modelcontextprotocol.io/specification/architecture/tasks/)
