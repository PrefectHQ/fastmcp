"""Tests for Docket integration in FastMCP."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from docket import Docket
from docket.worker import Worker
from fastmcp_tasks.dependencies import CurrentDocket, CurrentWorker

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.dependencies import get_context
from fastmcp_tasks import TasksExtension

HUZZAH = "huzzah!"


@pytest.fixture(autouse=True)
def reset_docket_memory_server():
    """Force a fresh memory:// Docket server bound to each test's event loop."""
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")
    yield
    if hasattr(Docket, "_memory_server"):
        delattr(Docket, "_memory_server")


async def test_docket_not_initialized_without_task_components():
    """Docket is only initialized when task-enabled components exist."""
    mcp = FastMCP("test-server")

    @mcp.tool()
    def regular_tool() -> str:
        return "no docket needed"

    async with Client(mcp) as client:
        # Without a task=True tool, the lifespan never takes the Docket branch.
        assert mcp.docket is None

        result = await client.call_tool("regular_tool", {})
        assert result.data == "no docket needed"


async def test_current_docket():
    """CurrentDocket dependency provides access to Docket instance."""
    mcp = FastMCP("test-server")
    mcp.add_extension(TasksExtension())

    # A task-enabled component makes the lifespan start Docket.
    @mcp.tool(task=True)
    async def _trigger_docket() -> str:
        return "trigger"

    @mcp.tool()
    def check_docket(docket: Docket = CurrentDocket()) -> str:
        assert isinstance(docket, Docket)
        return HUZZAH

    async with Client(mcp) as client:
        result = await client.call_tool("check_docket", {})
        assert HUZZAH in str(result)


async def test_current_worker():
    """CurrentWorker dependency provides access to Worker instance."""
    mcp = FastMCP("test-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def _trigger_docket() -> str:
        return "trigger"

    @mcp.tool()
    def check_worker(
        worker: Worker = CurrentWorker(),
        docket: Docket = CurrentDocket(),
    ) -> str:
        assert isinstance(worker, Worker)
        assert worker.docket is docket
        return HUZZAH

    async with Client(mcp) as client:
        result = await client.call_tool("check_worker", {})
        assert HUZZAH in str(result)


async def test_worker_executes_background_tasks():
    """Verify that the Docket Worker is running and executes tasks."""
    task_completed = asyncio.Event()
    mcp = FastMCP("test-server")
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def _trigger_docket() -> str:
        return "trigger"

    @mcp.tool()
    async def schedule_work(
        task_name: str,
        docket: Docket = CurrentDocket(),
    ) -> str:
        """Schedule a background task."""

        async def background_task(name: str):
            """Simple background task that signals completion."""
            task_completed.set()

        # Schedule the task (Worker running in background will execute it)
        await docket.add(background_task)(task_name)

        return f"Scheduled {task_name}"

    async with Client(mcp) as client:
        result = await client.call_tool("schedule_work", {"task_name": "test-task"})
        assert "Scheduled test-task" in str(result)

        # Wait for background task to execute (max 2 seconds)
        await asyncio.wait_for(task_completed.wait(), timeout=2.0)


async def test_concurrent_calls_maintain_isolation():
    """Multiple concurrent calls each get the same Docket instance."""
    mcp = FastMCP("test-server")
    mcp.add_extension(TasksExtension())
    docket_ids = []

    @mcp.tool(task=True)
    async def _trigger_docket() -> str:
        return "trigger"

    @mcp.tool()
    def capture_docket_id(call_num: int, docket: Docket = CurrentDocket()) -> str:
        docket_ids.append((call_num, id(docket)))
        return HUZZAH

    async with Client(mcp) as client:
        results = await asyncio.gather(
            client.call_tool("capture_docket_id", {"call_num": 1}),
            client.call_tool("capture_docket_id", {"call_num": 2}),
            client.call_tool("capture_docket_id", {"call_num": 3}),
        )

        for result in results:
            assert HUZZAH in str(result)

        # All calls should see the same Docket instance
        assert len(docket_ids) == 3
        first_id = docket_ids[0][1]
        assert all(docket_id == first_id for _, docket_id in docket_ids)


async def test_user_lifespan_still_works_with_docket():
    """User-provided lifespan works correctly alongside Docket."""
    lifespan_entered = False

    @asynccontextmanager
    async def custom_lifespan(server: FastMCP):
        nonlocal lifespan_entered
        lifespan_entered = True
        yield {"custom_data": "test_value"}

    mcp = FastMCP("test-server", lifespan=custom_lifespan)
    mcp.add_extension(TasksExtension())

    @mcp.tool(task=True)
    async def _trigger_docket() -> str:
        return "trigger"

    @mcp.tool()
    def check_both(docket: Docket = CurrentDocket()) -> str:
        assert isinstance(docket, Docket)
        ctx = get_context()
        assert ctx.request_context is not None
        lifespan_data = ctx.request_context.lifespan_context
        assert lifespan_data.get("custom_data") == "test_value"
        return HUZZAH

    async with Client(mcp) as client:
        assert lifespan_entered
        result = await client.call_tool("check_both", {})
        assert HUZZAH in str(result)
