"""Tests for concurrent dependency resolution in foreground and background tasks.

Regression tests for:
- #3654: ValueError when concurrent Docket tasks share a Dependency instance
         that stores a ContextVar token on `self`
- #3656: Progress raises AssertionError when concurrent tasks share `_impl`
"""

import asyncio

from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.dependencies import (
    Progress,
    get_access_token,
    get_http_headers,
)
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import (
    call_tool_without_optin,
    running_task_server,
    submit_task,
    wait_for_task,
)


async def test_concurrent_foreground_tools_with_context():
    """Multiple concurrent tool calls sharing the same Context() default
    should not raise ValueError from ContextVar token resets (#3654)."""
    mcp = FastMCP("test")
    results: list[str] = []

    @mcp.tool
    async def slow_tool(name: str, ctx: Context) -> str:
        await asyncio.sleep(0.01)
        results.append(name)
        return f"done:{name}"

    outcomes = await asyncio.gather(
        *[
            call_tool_without_optin(mcp, "slow_tool", {"name": f"task-{i}"})
            for i in range(4)
        ]
    )

    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome.content[0].text.startswith("done:")


async def test_concurrent_foreground_tools_with_progress():
    """Multiple concurrent tool calls sharing the same Progress() default
    should not raise AssertionError from _impl being None (#3656)."""
    mcp = FastMCP("test")

    @mcp.tool
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

    outcomes = await asyncio.gather(
        *[
            call_tool_without_optin(
                mcp, "variable_tool", {"name": f"t-{i}", "delay": 0.01 * (i + 1)}
            )
            for i in range(4)
        ]
    )

    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome.content[0].text.startswith("done:")


async def test_concurrent_background_tasks_with_context():
    """Multiple concurrent background tasks sharing Context() should
    not raise ValueError from ContextVar token resets (#3654)."""
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bg_tool(name: str, ctx: Context) -> str:
        await asyncio.sleep(0.01)
        return f"bg:{name}"

    async with running_task_server(mcp):
        created = [
            await submit_task(mcp, "bg_tool", {"name": f"bg-{i}"}) for i in range(4)
        ]
        finals = await asyncio.gather(*[wait_for_task(mcp, c.task_id) for c in created])

    assert len(finals) == 4
    for final in finals:
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"]["result"].startswith("bg:")


async def test_concurrent_background_tasks_with_progress():
    """Multiple concurrent background tasks sharing Progress() should
    not raise AssertionError from _impl being None (#3656)."""
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())

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

    async with running_task_server(mcp):
        created = [
            await submit_task(
                mcp,
                "bg_progress_tool",
                {"name": f"bg-{i}", "delay": 0.01 * (i + 1)},
            )
            for i in range(4)
        ]
        finals = await asyncio.gather(*[wait_for_task(mcp, c.task_id) for c in created])

    assert len(finals) == 4
    for final in finals:
        assert final.status == "completed"
        assert final.result is not None
        assert final.result["structuredContent"]["result"].startswith("bg:")


async def test_dependency_aenter_returns_fresh_instances():
    """Dependency.__aenter__ returns independent per-invocation objects,
    not the shared default."""
    mcp = FastMCP("test")

    instances: list[Context] = []

    @mcp.tool
    async def capture_context(ctx: Context) -> str:
        instances.append(ctx)
        return "ok"

    await asyncio.gather(
        call_tool_without_optin(mcp, "capture_context", {}),
        call_tool_without_optin(mcp, "capture_context", {}),
    )

    assert len(instances) == 2
    assert instances[0] is not instances[1]


async def test_progress_aenter_returns_fresh_instances():
    """Progress.__aenter__ returns independent per-invocation objects,
    not the shared default."""
    progress_instances: list[Progress] = []

    mcp = FastMCP("test")

    @mcp.tool
    async def capture_progress(progress: Progress = Progress()) -> str:
        progress_instances.append(progress)
        await progress.set_total(1)
        await progress.increment()
        return "ok"

    await asyncio.gather(
        call_tool_without_optin(mcp, "capture_progress", {}),
        call_tool_without_optin(mcp, "capture_progress", {}),
    )

    assert len(progress_instances) == 2
    assert progress_instances[0] is not progress_instances[1]
    assert progress_instances[0]._impl is not progress_instances[1]._impl


async def test_sync_context_functions_work_in_background_without_deps():
    """Sync helpers like get_http_headers() work in a background task even when
    the tool declares no Context or CurrentRequest dependency.

    This exercises the sync snapshot fallback path which must work with the
    memory:// (fakeredis) backend.
    """
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def bare_sync_access() -> dict[str, str]:
        headers = get_http_headers()
        return {"has_headers": str(bool(headers))}

    async with running_task_server(mcp):
        created = await submit_task(mcp, "bare_sync_access", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"] == {"has_headers": "False"}


async def test_sync_context_functions_work_in_background_with_context():
    """Sync helpers work via ContextVar when Context loads the snapshot."""
    mcp = FastMCP("test")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def context_sync_access(ctx: Context) -> dict[str, str]:
        headers = get_http_headers()
        token = get_access_token()
        return {
            "has_headers": str(bool(headers)),
            "has_token": str(token is not None),
            "is_background": str(ctx.is_background_task),
        }

    async with running_task_server(mcp):
        created = await submit_task(mcp, "context_sync_access", {})
        final = await wait_for_task(mcp, created.task_id)

    assert final.status == "completed"
    assert final.result is not None
    assert final.result["structuredContent"]["is_background"] == "True"
