"""Tests for distributed notification queue (SEP-1686).

Integration tests verify that the notification queue works end-to-end
using Client(mcp) with the real memory:// Docket backend.
No mocking of Redis, sessions, or Docket internals.
"""

import asyncio

import mcp.types as mcp_types

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.messages import MessageHandler
from fastmcp.server.context import Context
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.tasks.notifications import (
    get_subscriber_count,
)


class NotificationCaptureHandler(MessageHandler):
    """Capture server notifications for test assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.notifications: list[mcp_types.ServerNotification] = []

    async def on_notification(self, message: mcp_types.ServerNotification) -> None:
        self.notifications.append(message)

    def for_method(self, method: str) -> list[mcp_types.ServerNotification]:
        return [
            notification
            for notification in self.notifications
            if notification.root.method == method
        ]


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

        The elicitation relay handles the response via the client's
        elicitation_handler. We verify both the notification metadata
        structure and the end-to-end elicitation flow.
        """
        mcp = FastMCP("notification-test")
        notification_handler = NotificationCaptureHandler()

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
            message_handler=notification_handler,
            elicitation_handler=elicitation_handler,
        ) as client:
            task = await client.call_tool("elicit_tool", {}, task=True)

            await task.wait(timeout=10.0)
            result = await task.result()
            assert result.data == "got: hello"

            # Verify the input_required notification was delivered with metadata
            notification: mcp_types.ServerNotification | None = None
            candidates = notification_handler.for_method("notifications/tasks/status")
            for candidate in reversed(candidates):
                candidate_meta = getattr(candidate.root, "_meta", None)
                related_task = (
                    candidate_meta.get("modelcontextprotocol.io/related-task")
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
            task_meta = getattr(notification.root, "_meta", None)
            assert isinstance(task_meta, dict)

            related_task = task_meta.get("modelcontextprotocol.io/related-task")
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
        for _ in range(20):
            if get_subscriber_count() == count_before:
                break
            await asyncio.sleep(0.05)
        assert get_subscriber_count() == count_before
