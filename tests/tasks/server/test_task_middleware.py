"""Task-augmented calls flow through ToolResult-inspecting middleware safely.

A `tools/call` the tasks extension turns into a background task returns a
`CreateTaskResult` up through the middleware chain. Middleware that post-process
a `ToolResult` (response caching, response limiting) must pass that
acknowledgement through untouched rather than crash after the task is enqueued.
"""

from __future__ import annotations

from fastmcp_tasks.models import CreateTaskResult

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import running_task_server, submit_task, wait_for_task


async def test_tasked_call_survives_result_inspecting_middleware():
    mcp = FastMCP("tasks-mw")
    mcp.add_extension(TasksExtension())
    mcp.add_middleware(ResponseCachingMiddleware())
    mcp.add_middleware(ResponseLimitingMiddleware(max_size=1_000_000))

    @mcp.tool(task=True)
    async def crunch(n: int) -> int:
        return n * n

    async with running_task_server(mcp):
        created = await submit_task(mcp, "crunch", {"n": 9})
        assert isinstance(created, CreateTaskResult)
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"result": 81}
