"""Protocol-level task behavior for SEP-2663 tasks.

Generic protocol behaviors driven in-process via the task helpers: a submitted
task carries a server-generated id and a TTL, and a task whose tool raises
surfaces its error rather than a result.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    run_task,
    running_task_server,
    submit_task,
)


def _task_server() -> FastMCP:
    mcp = FastMCP("task-test-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def simple_tool(message: str) -> str:
        return f"Processed: {message}"

    @mcp.tool(task=True)
    async def failing_tool() -> str:
        raise ToolError("This tool always fails")

    return mcp


async def test_task_metadata_includes_task_id_and_ttl():
    """A submitted task carries a server-generated id and a positive TTL."""
    mcp = _task_server()
    async with running_task_server(mcp):
        created = await submit_task(mcp, "simple_tool", {"message": "test"})
        assert isinstance(created.task_id, str)
        assert created.task_id
        assert created.ttl_ms is not None and created.ttl_ms > 0


async def test_raised_tool_error_completes_with_is_error():
    """A task whose tool raises completes with an is_error result (SEP-2663).

    `failed` is reserved for protocol faults; a raised tool error is the same
    `isError` result a live tools/call returns.
    """
    mcp = _task_server()
    async with running_task_server(mcp):
        final = await run_task(mcp, "failing_tool", {})
        assert final.status == "completed"
        assert final.error is None
        assert final.result is not None
        assert final.result["isError"] is True
        assert "This tool always fails" in final.result["content"][0]["text"]
