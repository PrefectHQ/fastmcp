"""Tests for concurrent dependency resolution in foreground and background tasks.

Regression tests for:
- #3654: ValueError when concurrent Docket tasks share a Dependency instance
         that stores a ContextVar token on `self`
- #3656: Progress raises AssertionError when concurrent tasks share `_impl`
"""

import asyncio

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.dependencies import Progress
from fastmcp.server.context import Context
from fastmcp.server.dependencies import (
    get_access_token,
    get_http_headers,
)


async def test_concurrent_foreground_tools_with_context():
    """Multiple concurrent tool calls sharing the same CurrentContext() default
    should not raise ValueError from ContextVar token resets (#3654)."""
    mcp = FastMCP("test")
    results: list[str] = []

    @mcp.tool()
    async def slow_tool(name: str, ctx: Context) -> str:
        await asyncio.sleep(0.05)
        results.append(name)
        return f"done:{name}"

    async with Client(mcp) as client:
        tasks = [client.call_tool("slow_tool", {"name": f"task-{i}"}) for i in range(4)]
        outcomes = await asyncio.gather(*tasks)

    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome.content[0].text.startswith("done:")


async def test_concurrent_foreground_tools_with_progress():
    """Multiple concurrent tool calls sharing the same Progress() default
    should not raise AssertionError from _impl being None (#3656)."""
    mcp = FastMCP("test")

    @mcp.tool()
    async def variable_tool(
        name: str, delay: float, progress: Progress = Progress()
    ) -> str:
        await progress.set_total(3)
        await progress.increment()
        await asyncio.sleep(delay)
        await progress.increment()
        await progress.set_message(f"finishing {name}")
        await progress.increment()
        return f"done:{name}"

    async with Client(mcp) as client:
        tasks = [
            client.call_tool(
                "variable_tool", {"name": f"t-{i}", "delay": 0.01 * (i + 1)}
            )
            for i in range(4)
        ]
        outcomes = await asyncio.gather(*tasks)

    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome.content[0].text.startswith("done:")


async def test_concurrent_background_tasks_with_context():
    """Multiple concurrent background tasks sharing _CurrentContext() should
    not raise ValueError from ContextVar token resets (#3654)."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def bg_tool(name: str, ctx: Context) -> str:
        await asyncio.sleep(0.05)
        return f"bg:{name}"

    async with Client(mcp) as client:
        task_handles = [
            await client.call_tool("bg_tool", {"name": f"bg-{i}"}, task=True)
            for i in range(4)
        ]
        results = await asyncio.gather(*[t.result() for t in task_handles])

    assert len(results) == 4
    for result in results:
        assert result.content[0].text.startswith("bg:")


async def test_concurrent_background_tasks_with_progress():
    """Multiple concurrent background tasks sharing Progress() should
    not raise AssertionError from _impl being None (#3656)."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def bg_progress_tool(
        name: str, delay: float, progress: Progress = Progress()
    ) -> str:
        await progress.set_total(3)
        await progress.increment()
        await asyncio.sleep(delay)
        await progress.increment()
        await progress.set_message(f"bg finishing {name}")
        await progress.increment()
        return f"bg:{name}"

    async with Client(mcp) as client:
        task_handles = [
            await client.call_tool(
                "bg_progress_tool",
                {"name": f"bg-{i}", "delay": 0.01 * (i + 1)},
                task=True,
            )
            for i in range(4)
        ]
        results = await asyncio.gather(*[t.result() for t in task_handles])

    assert len(results) == 4
    for result in results:
        assert result.content[0].text.startswith("bg:")


async def test_dependency_aenter_returns_fresh_instances():
    """Verify that Dependency.__aenter__ returns independent per-invocation
    objects, not the shared default."""
    mcp = FastMCP("test")

    instances: list[Context] = []

    @mcp.tool()
    async def capture_context(ctx: Context) -> str:
        instances.append(ctx)
        return "ok"

    async with Client(mcp) as client:
        await asyncio.gather(
            client.call_tool("capture_context", {}),
            client.call_tool("capture_context", {}),
        )

    assert len(instances) == 2
    assert instances[0] is not instances[1]


async def test_progress_aenter_returns_fresh_instances():
    """Verify that Progress.__aenter__ returns independent per-invocation
    objects, not the shared default."""
    progress_instances: list[Progress] = []

    mcp = FastMCP("test")

    @mcp.tool()
    async def capture_progress(progress: Progress = Progress()) -> str:
        progress_instances.append(progress)
        await progress.set_total(1)
        await progress.increment()
        return "ok"

    async with Client(mcp) as client:
        await asyncio.gather(
            client.call_tool("capture_progress", {}),
            client.call_tool("capture_progress", {}),
        )

    assert len(progress_instances) == 2
    assert progress_instances[0] is not progress_instances[1]
    assert progress_instances[0]._impl is not progress_instances[1]._impl


async def test_sync_context_functions_work_in_background_without_deps():
    """Sync functions like get_http_request() should work in background tasks
    even when the tool declares no Context or CurrentRequest dependency.

    This exercises the sync Redis fallback path (_get_task_snapshot_sync →
    _load_snapshot_sync_redis) which must work with both memory:// (fakeredis)
    and real Redis backends.
    """
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def bare_sync_access() -> dict[str, str]:
        headers = get_http_headers()
        return {"has_headers": str(bool(headers))}

    async with Client(mcp) as client:
        task = await client.call_tool("bare_sync_access", {}, task=True)
        result = await task.result()
        assert result.data == {"has_headers": "False"}


async def test_sync_context_functions_work_in_background_with_context():
    """Sync functions work via ContextVar when _CurrentContext loads the snapshot."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def context_sync_access(ctx: Context) -> dict[str, str]:
        headers = get_http_headers()
        token = get_access_token()
        return {
            "has_headers": str(bool(headers)),
            "has_token": str(token is not None),
            "is_background": str(ctx.is_background_task),
        }

    async with Client(mcp) as client:
        task = await client.call_tool("context_sync_access", {}, task=True)
        result = await task.result()
        assert result.data["is_background"] == "True"
