from dataclasses import dataclass, field
from datetime import datetime

import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.server.context import Context


@dataclass
class NotificationRecording:
    """Record of a notification that was received."""

    method: str
    notification: mcp.types.ServerNotification
    timestamp: datetime = field(default_factory=datetime.now)


class RecordingMessageHandler(MessageHandler):
    """A message handler that records all notifications."""

    def __init__(self, name: str | None = None):
        super().__init__()
        self.notifications: list[NotificationRecording] = []
        self.name = name

    async def on_notification(self, message: mcp.types.ServerNotification) -> None:
        """Record all notifications with timestamp."""
        self.notifications.append(
            NotificationRecording(method=message.root.method, notification=message)
        )

    def get_notifications(
        self, method: str | None = None
    ) -> list[NotificationRecording]:
        """Get all recorded notifications, optionally filtered by method."""
        if method is None:
            return self.notifications
        return [n for n in self.notifications if n.method == method]

    def assert_notification_sent(self, method: str, times: int = 1) -> bool:
        """Assert that a notification was sent a specific number of times."""
        notifications = self.get_notifications(method)
        actual_times = len(notifications)
        assert actual_times == times, (
            f"Expected {times} notifications for {method}, "
            f"but received {actual_times} notifications"
        )
        return True

    def assert_notification_not_sent(self, method: str) -> bool:
        """Assert that a notification was not sent."""
        notifications = self.get_notifications(method)
        assert len(notifications) == 0, (
            f"Expected no notifications for {method}, but received {len(notifications)}"
        )
        return True

    def reset(self):
        """Clear all recorded notifications."""
        self.notifications.clear()


@pytest.fixture
def recording_message_handler():
    """Fixture that provides a recording message handler instance."""
    handler = RecordingMessageHandler(name="recording_message_handler")
    yield handler


class TestNotificationAPI:
    """Test the notification API."""

    async def test_send_notification_async(
        self,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that send_notification sends immediately in async context."""
        server = FastMCP(name="NotificationAPITestServer")

        @server.tool
        async def trigger_notification(ctx: Context) -> str:
            """Send a notification using the async API."""
            await ctx.send_notification(mcp.types.ToolListChangedNotification())
            return "Notification sent"

        async with Client(server, message_handler=recording_message_handler) as client:
            recording_message_handler.reset()
            await client.call_tool("trigger_notification", {})

            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=1
            )

    async def test_send_multiple_notifications(
        self,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test sending multiple different notification types."""
        server = FastMCP(name="NotificationAPITestServer")

        @server.tool
        async def trigger_all_notifications(ctx: Context) -> str:
            """Send all notification types."""
            await ctx.send_notification(mcp.types.ToolListChangedNotification())
            await ctx.send_notification(mcp.types.ResourceListChangedNotification())
            await ctx.send_notification(mcp.types.PromptListChangedNotification())
            return "All notifications sent"

        async with Client(server, message_handler=recording_message_handler) as client:
            recording_message_handler.reset()
            await client.call_tool("trigger_all_notifications", {})

            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=1
            )
            recording_message_handler.assert_notification_sent(
                "notifications/resources/list_changed", times=1
            )
            recording_message_handler.assert_notification_sent(
                "notifications/prompts/list_changed", times=1
            )
