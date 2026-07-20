"""Tests for distributed notification queue (SEP-1686).

Integration tests verify that the notification queue works end-to-end
using Client(mcp) with the real memory:// Docket backend.
No mocking of Redis, sessions, or Docket internals.
"""

import asyncio
import time

import mcp_types

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.context import Context
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.tasks.notifications import (
    get_subscriber_count,
)


class TestNotificationIntegration:
    """Integration tests for the notification queue using real Docket memory backend.

    The elicitation flow validates the full notification pipeline:
    1. Tool calls ctx.elicit() -> stores request in Redis -> pushes notification
    2. Subscriber picks up notification -> sends MCP notification to client
    3. Subscriber relays elicitation/create to client -> handler responds
    4. Relay pushes response to Redis -> BLPOP wakes tool
    """

    async def test_notification_delivered_during_elicitation(self):
        """Full E2E: notification queue delivers input_required metadata to client.

        SDK v2 does not carry `notifications/tasks/status` in any protocol
        version's core notification tables, so it is delivered through the
        client's task-status notification binding (routed to Task objects) rather
        than the message_handler. We observe it via `on_status_change`, whose
        GetTaskResult carries the notification's `_meta`.
        """
        mcp = FastMCP("notification-test")
        captured: list[mcp_types.GetTaskResult] = []

        @mcp.tool(task=True)
        async def elicit_tool(ctx: Context) -> str:
            result = await ctx.elicit("Enter value", str)
            if isinstance(result, AcceptedElicitation):
                return f"got: {result.data}"
            return "no value"

        async def elicitation_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"value": "hello"})

        async with Client(
            mcp,
            elicitation_handler=elicitation_handler,
        ) as client:
            task = await client.call_tool("elicit_tool", {}, task=True)
            task.on_status_change(captured.append)

            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "got: hello"

            # Verify the input_required notification was delivered with metadata
            notification: mcp_types.GetTaskResult | None = None
            for candidate in reversed(captured):
                candidate_meta = candidate.meta
                related_task = (
                    candidate_meta.get("io.modelcontextprotocol/related-task")
                    if isinstance(candidate_meta, dict)
                    else None
                )
                if (
                    isinstance(related_task, dict)
                    and related_task.get("status") == "input_required"
                ):
                    notification = candidate
                    break

            assert notification is not None, "expected notifications/tasks/status"
            task_meta = notification.meta
            assert isinstance(task_meta, dict)

            related_task = task_meta.get("io.modelcontextprotocol/related-task")
            assert isinstance(related_task, dict)
            assert related_task.get("taskId") == task.task_id
            assert related_task.get("status") == "input_required"

            elicitation = related_task.get("elicitation")
            assert isinstance(elicitation, dict)
            assert elicitation.get("message") == "Enter value"
            assert isinstance(elicitation.get("requestId"), str)
            assert isinstance(elicitation.get("requestedSchema"), dict)

    async def test_subscriber_started_and_cleaned_up(self):
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
        deadline = time.monotonic() + 1.0
        while get_subscriber_count() != count_before and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert get_subscriber_count() == count_before
