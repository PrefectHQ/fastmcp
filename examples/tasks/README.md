# FastMCP Background Tasks Example

A runnable client/server pair for SEP-2663 background tasks. The server exposes
one `task=True` tool that reports progress as it works; the client drives it
three ways — transparently, through an explicit handle, and several at once in
parallel.

This runs on the in-memory backend by default, so there's nothing to install or
start beyond the two processes.

## Run it

In one terminal, start the server:

```bash
uv sync                              # from the fastmcp root, once
python examples/tasks/server.py      # listens on http://127.0.0.1:8000/mcp
```

In another terminal, drive it from the client:

```bash
# Transparent — call_tool runs the background task and returns its result
python examples/tasks/client.py --duration 8

# Explicit handle — returns immediately, poll it yourself, then collect
python examples/tasks/client.py handle --duration 6

# Parallel — fire several tasks at once and watch them overlap
python examples/tasks/client.py parallel
python examples/tasks/client.py parallel 8 6 4 2
```

The `parallel` run is the one to watch: four tasks of decreasing duration all
start at once and total wall-clock tracks the *longest* task rather than the
sum, because the worker runs them concurrently.

## How it works

The server enables tasks with one line:

```python
mcp = FastMCP("Tasks Example")
mcp.add_extension(TasksExtension())
```

The client opts in by importing `fastmcp_tasks` (which it does to use
`call_tool_task`). That single import enables task support for every `Client`
in the process — without it, a `Client` never advertises the tasks capability,
so the server would run the calls synchronously.

## Distributed workers (optional)

The default `memory://` backend runs the worker in the server process. To run
workers as separate processes, point Docket at Redis and start it first:

```bash
cd examples/tasks
docker compose up -d
export FASTMCP_DOCKET_URL=redis://localhost:24242/0   # or: direnv allow

python server.py                                        # in one terminal
python -m fastmcp_tasks.worker_cli worker server.py     # extra worker(s) in others
```

| Backend      | Workers                         |
| ------------ | ------------------------------- |
| `memory://`  | in-process only (default)       |
| `redis://…`  | distributed across processes    |

## Learn more

- [Server background tasks](https://gofastmcp.com/servers/tasks)
- [Client background tasks](https://gofastmcp.com/clients/tasks)
- [Docket](https://github.com/chrisguidry/docket)
