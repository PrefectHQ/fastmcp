# fastmcp-tasks

A complete implementation of background tasks for the Model Context Protocol — the `io.modelcontextprotocol/tasks` extension defined in [SEP-2663](https://github.com/modelcontextprotocol/ext-tasks).

The MCP tasks extension is a Final SEP, but as of this writing it ships in the ecosystem as a schema and a prose specification — no language SDK provides a working runtime for it. `fastmcp-tasks` is, to our knowledge, the first: a full server-side implementation of the protocol, built on the durable execution engine ([docket](https://github.com/chrisguidry/docket)) that FastMCP has run in production since v3. If you want to actually *run* MCP background tasks today, this is the implementation.

## What background tasks are

Most tool calls are synchronous: the client sends `tools/call` and holds the request open until the tool returns. That breaks down for work that takes minutes or hours — a long analysis, a batch job, a slow external API. The tasks extension lets a server answer such a call *immediately* with a durable task handle, then run the work in the background while the client polls for completion on its own schedule.

The model is poll-based and stateless by construction, which is what makes it survive disconnects, server restarts, and load balancers:

1. A client that supports tasks issues a normal `tools/call` with a per-request opt-in.
2. The server decides whether to run it as a task. If it does, it returns a `CreateTaskResult` carrying a server-generated task id — right away, before the work starts.
3. The client polls `tasks/get` until the task reaches a terminal state, then reads the result inlined in the response.
4. `tasks/cancel` requests cancellation; `tasks/update` answers any input the task asks for mid-run.

The server owns the task's durable state, so the client can poll across independent requests — from any process, after a crash, through any replica — with no session affinity required.

## Usage

Install it as the `tasks` extra on FastMCP:

```bash
uv pip install "fastmcp[tasks]"
```

Register the extension on your server and mark the tools that may run as tasks. The extension is where the backend is configured — point it at Redis for a distributed deployment, or leave it on the in-memory default for a single process:

```python
from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension

mcp = FastMCP("Analytics")
mcp.add_extension(TasksExtension(url="redis://localhost:6379/0"))


@mcp.tool(task=True)
async def analyze(dataset: str) -> str:
    # Long-running work. The client gets a task handle immediately and
    # polls for the result; this runs in a background worker.
    ...
```

`task=True` is a declaration of intent — this tool *may* run as a task — while the server, per the spec, decides per call whether to actually task it. Use `TaskConfig` for finer control:

```python
from fastmcp.utilities.tasks import TaskConfig


@mcp.tool(task=TaskConfig(mode="required"))
async def must_run_async(n: int) -> int:
    # Always runs as a task; a client that has not opted in is told so.
    ...
```

Registering `TasksExtension` is required to serve `task=True` tools — the tool declares intent, the extension provides the engine. A `task=True` tool on a server with no tasks extension registered fails loudly at startup rather than silently running inline.

### Running out-of-process workers

For distributed deployments backed by Redis, run dedicated worker processes alongside your server:

```bash
python -m fastmcp_tasks.worker_cli worker server.py
```

Workers and servers that share a backend URL and queue name share a task queue, so you can scale execution independently of your request-serving frontends.

## Configuration

The backend is configured on the extension. Every option also has a `FASTMCP_DOCKET_*` environment variable, so an env-configured deployment can construct `TasksExtension()` with no arguments:

| Option | Env var | Default | Description |
| --- | --- | --- | --- |
| `url` | `FASTMCP_DOCKET_URL` | `memory://` | Backend URL. `memory://` for single-process; `redis://host:port/db` for distributed workers. |
| `name` | `FASTMCP_DOCKET_NAME` | `fastmcp` | Queue name. Servers and workers sharing a name and URL share a queue. |
| `concurrency` | `FASTMCP_DOCKET_CONCURRENCY` | `10` | Maximum concurrent tasks per worker. |

See the [FastMCP task documentation](https://gofastmcp.com/servers/tasks) for the full reference.

## Status

The tasks extension is an experimental MCP extension, and `fastmcp-tasks` tracks its draft schema. The protocol's shape is settled — SEP-2663 is Final — but field-level details may still move; this package versions independently so it can follow the schema without waiting on a FastMCP release.
