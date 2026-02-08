"""Tests for distributed notification queue (SEP-1686).

Integration tests verify that the notification queue works end-to-end
using Client(mcp) with the real memory:// Docket backend.
No mocking of Redis, sessions, or Docket internals.
"""

import asyncio

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.context import Context
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.tasks.elicitation import handle_task_input
from fastmcp.server.tasks.notifications import (
    get_subscriber_count,
)


class TestNotificationIntegration:
    """Integration tests for the notification queue using real Docket memory backend.

    The elicitation flow implicitly validates the full notification pipeline:
    1. Tool calls ctx.elicit() → stores request in Redis → pushes notification
    2. Subscriber picks up notification → sends MCP notification to client
    3. Client calls handle_task_input() → LPUSH response → BLPOP wakes tool
    """

    async def test_notification_delivered_during_elicitation(self):
        """Full E2E: notification queue delivers elicitation notification to client."""
        mcp = FastMCP("notification-test")
        elicit_started = asyncio.Event()
        captured: dict[str, str | None] = {"task_id": None, "session_id": None}

        @mcp.tool(task=True)
        async def elicit_tool(ctx: Context) -> str:
            captured["task_id"] = ctx.task_id
            captured["session_id"] = ctx.session_id
            elicit_started.set()

            result = await ctx.elicit("Enter value", str)
            if isinstance(result, AcceptedElicitation):
                return f"got: {result.data}"
            return "no value"

        async with Client(mcp) as client:
            task = await client.call_tool("elicit_tool", {}, task=True)
            await asyncio.wait_for(elicit_started.wait(), timeout=5.0)

            # Poll until the task is waiting for input
            success = False
            for _ in range(40):
                success = await handle_task_input(
                    task_id=captured["task_id"],
                    session_id=captured["session_id"],
                    action="accept",
                    content={"value": "hello"},
                    fastmcp=mcp,
                )
                if success:
                    break
                await asyncio.sleep(0.05)

            assert success is True

            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "got: hello"

    async def test_subscriber_lifecycle(self):
        """Subscriber starts during background task and stops when client disconnects."""
        mcp = FastMCP("subscriber-test")
        tool_started = asyncio.Event()
        tool_continue = asyncio.Event()

        @mcp.tool(task=True)
        async def lifecycle_tool(ctx: Context) -> str:
            tool_started.set()
            await asyncio.wait_for(tool_continue.wait(), timeout=10.0)
            return "done"

        count_before = get_subscriber_count()

        async with Client(mcp) as client:
            task = await client.call_tool("lifecycle_tool", {}, task=True)
            await asyncio.wait_for(tool_started.wait(), timeout=5.0)

            # While a background task is running, subscriber should be active
            count_during = get_subscriber_count()
            assert count_during > count_before

            # Let the tool complete
            tool_continue.set()
            await task.wait(timeout=5.0)
            result = await task.result()
            assert result.data == "done"

        # After client disconnects, subscriber should be cleaned up
        # Allow brief time for async cleanup
        for _ in range(20):
            if get_subscriber_count() == count_before:
                break
            await asyncio.sleep(0.05)
        assert get_subscriber_count() == count_before
