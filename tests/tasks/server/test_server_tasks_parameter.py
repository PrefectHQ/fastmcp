"""Server-level `tasks` default inheritance and per-tool override (tools only).

`FastMCP(tasks=...)` sets the default task mode for tools; a per-tool `task=`
overrides it. SEP-2663 tasks are tools-only, so prompt/resource/template
inheritance is not covered. Tasking is driven in-process through the interceptor.
"""

from __future__ import annotations

from fastmcp_tasks.models import CreateTaskResult

from fastmcp import FastMCP
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    _opted_in_request,
    auth_scope,
    run_task,
    running_task_server,
    submit_task,
)


async def _opted_in_call(server: FastMCP, name: str, arguments: dict | None = None):
    """Run a `tools/call` WITH the tasks opt-in bound (used to prove sync paths)."""
    with auth_scope(None), _opted_in_request(name, arguments or {}, None):
        return await server.call_tool(name, arguments or {})


async def test_tool_inherits_server_default_true():
    """A tool inherits the server's tasks=True default and tasks when opted in."""
    mcp = FastMCP("test", tasks=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool
    async def my_tool() -> str:
        return "tool result"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "my_tool")
        assert isinstance(created, CreateTaskResult)


async def test_tool_inherits_server_default_false():
    """A tool inherits the server's tasks=False default and runs synchronously."""
    mcp = FastMCP("test", tasks=False)

    @mcp.tool
    async def my_tool() -> str:
        return "tool result"

    result = await _opted_in_call(mcp, "my_tool")
    assert not isinstance(result, CreateTaskResult)
    assert result.structured_content == {"result": "tool result"}


async def test_server_tasks_none_defaults_to_forbidden():
    """A server with tasks omitted defaults tools to forbidden (runs sync)."""
    mcp = FastMCP("test")  # tasks omitted -> forbidden default

    @mcp.tool
    async def my_tool() -> str:
        return "tool result"

    result = await _opted_in_call(mcp, "my_tool")
    assert not isinstance(result, CreateTaskResult)
    assert result.structured_content == {"result": "tool result"}


async def test_per_tool_true_overrides_server_false():
    """A per-tool task=True overrides the server default of tasks=False."""
    mcp = FastMCP("test", tasks=False)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def task_tool() -> str:
        return "background result"

    @mcp.tool
    async def default_tool() -> str:
        return "immediate result"

    async with running_task_server(mcp):
        created = await submit_task(mcp, "task_tool")
        assert isinstance(created, CreateTaskResult)

        # The inherited-forbidden tool still runs synchronously despite the opt-in.
        result = await _opted_in_call(mcp, "default_tool")
        assert not isinstance(result, CreateTaskResult)
        assert result.structured_content == {"result": "immediate result"}


async def test_per_tool_false_overrides_server_true():
    """A per-tool task=False overrides the server default of tasks=True."""
    mcp = FastMCP("test", tasks=True)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=False)
    async def no_task_tool() -> str:
        return "immediate result"

    @mcp.tool
    async def default_tool() -> str:
        return "background result"

    async with running_task_server(mcp):
        # Explicit False runs synchronously even when opted in.
        result = await _opted_in_call(mcp, "no_task_tool")
        assert not isinstance(result, CreateTaskResult)
        assert result.structured_content == {"result": "immediate result"}

        # The inherited-optional tool tasks when opted in.
        created = await submit_task(mcp, "default_tool")
        assert isinstance(created, CreateTaskResult)


async def test_task_with_custom_tool_name():
    """Tools registered under a custom name task correctly (issue #2642).

    When a tool is registered with a custom name different from the function
    name, task execution uses the custom name for Docket lookup.
    """
    mcp = FastMCP("test", tasks=True)
    mcp.add_extension(TasksExtension())

    async def my_function() -> str:
        return "result from custom-named tool"

    mcp.tool(my_function, name="custom-tool-name")

    async with running_task_server(mcp):
        final = await run_task(mcp, "custom-tool-name")
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"] == {
            "result": "result from custom-named tool"
        }
