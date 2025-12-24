from dataclasses import dataclass, field
from datetime import datetime, timedelta

import anyio
import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.server.context import Context
from fastmcp.tools.tool import Tool


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


@pytest.fixture
def notification_test_server(recording_message_handler):
    """Create a server for testing notifications."""
    mcp = FastMCP(name="NotificationTestServer")

    # Create a target tool that can be enabled/disabled
    def target_tool() -> str:
        """A tool that can be enabled/disabled."""
        return "Target tool executed"

    target_tool_obj = Tool.from_function(target_tool)
    mcp.add_tool(target_tool_obj)

    # Tool to enable the target tool
    @mcp.tool
    def enable_target_tool(ctx: Context) -> str:
        """Enable the target tool."""
        # Find and enable the target tool
        try:
            ctx.fastmcp.enable(keys=["tool:target_tool"])
            return "Target tool enabled"
        except Exception:
            return "Target tool not found"

    # Tool to disable the target tool
    @mcp.tool
    def disable_target_tool(ctx: Context) -> str:
        """Disable the target tool."""
        # Find and disable the target tool
        try:
            ctx.fastmcp.disable(keys=["tool:target_tool"])
            return "Target tool disabled"
        except Exception:
            return "Target tool not found"

    return mcp


class TestToolNotifications:
    """Test tool list changed notifications."""

    async def test_tool_enable_sends_notification(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that enabling a tool sends a tool list changed notification."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            # First disable the tool so we can test enabling it
            await client.call_tool("disable_target_tool", {})

            # Reset any notifications from the disable
            recording_message_handler.reset()

            # Now enable the target tool - this should trigger a notification
            result = await client.call_tool("enable_target_tool", {})
            assert result.data == "Target tool enabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=1
            )

    async def test_tool_disable_sends_notification(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that disabling a tool sends a tool list changed notification."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            # Reset any initialization notifications
            recording_message_handler.reset()

            # Disable the target tool
            result = await client.call_tool("disable_target_tool", {})
            assert result.data == "Target tool disabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=1
            )

    async def test_multiple_tool_changes_sends_notifications_per_change(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that notifications are only sent when state actually changes."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            # Reset any initialization notifications
            recording_message_handler.reset()

            # Tool starts enabled, so first enable is a no-op (no notification)
            await client.call_tool("enable_target_tool", {})
            # Disable changes state (notification)
            await client.call_tool("disable_target_tool", {})
            # Enable changes state (notification)
            await client.call_tool("enable_target_tool", {})

            # Should have 2 notifications (only the actual state changes)
            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=2
            )

    async def test_no_notification_when_no_state_change(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that no notification is sent when enable/disable doesn't change state."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            # Reset any initialization notifications
            recording_message_handler.reset()

            # Tool starts enabled, so enabling it again is a no-op
            await client.call_tool("enable_target_tool", {})

            # No notification should be sent
            recording_message_handler.assert_notification_not_sent(
                "notifications/tools/list_changed"
            )


@pytest.fixture
def resource_notification_test_server(recording_message_handler):
    """Create a server for testing resource notifications."""
    mcp = FastMCP(name="ResourceNotificationTestServer")

    # Create a target resource that can be enabled/disabled
    @mcp.resource("resource://target")
    def target_resource() -> str:
        """A resource that can be enabled/disabled."""
        return "Target resource content"

    # Tool to enable the target resource
    @mcp.tool
    def enable_target_resource(ctx: Context) -> str:
        """Enable the target resource."""
        try:
            ctx.fastmcp.enable(keys=["resource:resource://target"])
            return "Target resource enabled"
        except Exception:
            return "Target resource not found"

    # Tool to disable the target resource
    @mcp.tool
    def disable_target_resource(ctx: Context) -> str:
        """Disable the target resource."""
        try:
            ctx.fastmcp.disable(keys=["resource:resource://target"])
            return "Target resource disabled"
        except Exception:
            return "Target resource not found"

    return mcp


class TestResourceNotifications:
    """Test resource list changed notifications."""

    async def test_resource_enable_sends_notification(
        self,
        resource_notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that enabling a resource sends a resource list changed notification."""
        async with Client(
            resource_notification_test_server, message_handler=recording_message_handler
        ) as client:
            # First disable the resource so we can test enabling it
            await client.call_tool("disable_target_resource", {})

            # Reset any notifications from the disable
            recording_message_handler.reset()

            # Now enable the target resource - this should trigger a notification
            result = await client.call_tool("enable_target_resource", {})
            assert result.data == "Target resource enabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/resources/list_changed", times=1
            )

    async def test_resource_disable_sends_notification(
        self,
        resource_notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that disabling a resource sends a resource list changed notification."""
        async with Client(
            resource_notification_test_server, message_handler=recording_message_handler
        ) as client:
            # Reset any initialization notifications
            recording_message_handler.reset()

            # Disable the target resource
            result = await client.call_tool("disable_target_resource", {})
            assert result.data == "Target resource disabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/resources/list_changed", times=1
            )


@pytest.fixture
def prompt_notification_test_server(recording_message_handler):
    """Create a server for testing prompt notifications."""
    mcp = FastMCP(name="PromptNotificationTestServer")

    # Create a target prompt that can be enabled/disabled
    @mcp.prompt
    def target_prompt() -> str:
        """A prompt that can be enabled/disabled."""
        return "Target prompt content"

    # Tool to enable the target prompt
    @mcp.tool
    def enable_target_prompt(ctx: Context) -> str:
        """Enable the target prompt."""
        try:
            ctx.fastmcp.enable(keys=["prompt:target_prompt"])
            return "Target prompt enabled"
        except Exception:
            return "Target prompt not found"

    # Tool to disable the target prompt
    @mcp.tool
    def disable_target_prompt(ctx: Context) -> str:
        """Disable the target prompt."""
        try:
            ctx.fastmcp.disable(keys=["prompt:target_prompt"])
            return "Target prompt disabled"
        except Exception:
            return "Target prompt not found"

    return mcp


class TestPromptNotifications:
    """Test prompt list changed notifications."""

    async def test_prompt_enable_sends_notification(
        self,
        prompt_notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that enabling a prompt sends a prompt list changed notification."""
        async with Client(
            prompt_notification_test_server, message_handler=recording_message_handler
        ) as client:
            # First disable the prompt so we can test enabling it
            await client.call_tool("disable_target_prompt", {})

            # Reset any notifications from the disable
            recording_message_handler.reset()

            # Now enable the target prompt - this should trigger a notification
            result = await client.call_tool("enable_target_prompt", {})
            assert result.data == "Target prompt enabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/prompts/list_changed", times=1
            )

    async def test_prompt_disable_sends_notification(
        self,
        prompt_notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that disabling a prompt sends a prompt list changed notification."""
        async with Client(
            prompt_notification_test_server, message_handler=recording_message_handler
        ) as client:
            # Reset any initialization notifications
            recording_message_handler.reset()

            # Disable the target prompt
            result = await client.call_tool("disable_target_prompt", {})
            assert result.data == "Target prompt disabled"

            # Check that notification was sent
            recording_message_handler.assert_notification_sent(
                "notifications/prompts/list_changed", times=1
            )


class TestMessageHandlerGeneral:
    """Test the message handler functionality in general."""

    async def test_message_handler_receives_all_notifications(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that the message handler receives all types of notifications."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            recording_message_handler.reset()

            # Trigger a tool notification by disabling (tool starts enabled)
            await client.call_tool("disable_target_tool", {})

            # Verify the handler received the notification
            all_notifications = recording_message_handler.get_notifications()
            assert len(all_notifications) == 1
            assert all_notifications[0].method == "notifications/tools/list_changed"

    async def test_message_handler_notification_filtering(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that notification filtering works correctly."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            recording_message_handler.reset()

            # Trigger tool notifications (disable then enable to get 2 actual state changes)
            await client.call_tool("disable_target_tool", {})
            await client.call_tool("enable_target_tool", {})

            # Test filtering
            tool_notifications = recording_message_handler.get_notifications(
                "notifications/tools/list_changed"
            )
            assert len(tool_notifications) == 2

            # Test non-existent filter
            resource_notifications = recording_message_handler.get_notifications(
                "notifications/resources/list_changed"
            )
            assert len(resource_notifications) == 0

    async def test_notification_structure(
        self,
        notification_test_server: FastMCP,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that notifications have the correct structure."""
        async with Client(
            notification_test_server, message_handler=recording_message_handler
        ) as client:
            recording_message_handler.reset()

            # Trigger a notification by disabling (tool starts enabled)
            await client.call_tool("disable_target_tool", {})

            # Check notification structure
            notifications = recording_message_handler.get_notifications(
                "notifications/tools/list_changed"
            )
            assert len(notifications) == 1

            notification = notifications[0]
            assert isinstance(notification.notification, mcp.types.ServerNotification)
            assert isinstance(
                notification.notification.root, mcp.types.ToolListChangedNotification
            )
            assert (
                notification.notification.root.method
                == "notifications/tools/list_changed"
            )


class TestNotificationAPI:
    """Test the new unified notification API."""

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

    async def test_send_notification_sync(
        self,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that send_notification_sync queues for background sending."""
        server = FastMCP(name="NotificationAPITestServer")

        @server.tool
        def trigger_notification_sync(ctx: Context) -> str:
            """Send a notification using the sync API."""
            ctx.send_notification_sync(mcp.types.ToolListChangedNotification())
            return "Notification queued"

        async with Client(server, message_handler=recording_message_handler) as client:
            recording_message_handler.reset()
            await client.call_tool("trigger_notification_sync", {})

            # Notification should have been sent by the background flusher or final flush
            recording_message_handler.assert_notification_sent(
                "notifications/tools/list_changed", times=1
            )

    async def test_send_notification_sync_background_flusher(
        self,
        recording_message_handler: RecordingMessageHandler,
    ):
        """Test that background flusher sends notifications within ~1 second."""
        server = FastMCP(name="NotificationAPITestServer")

        @server.tool
        async def trigger_and_wait(ctx: Context) -> str:
            """Queue a notification and wait, then return timestamp."""
            ctx.send_notification_sync(mcp.types.ToolListChangedNotification())

            # Wait 3 seconds - background flusher runs every 1 second
            await anyio.sleep(3)

            return datetime.now().isoformat()

        async with Client(server, message_handler=recording_message_handler) as client:
            recording_message_handler.reset()
            result = await client.call_tool("trigger_and_wait", {})
            tool_finished = datetime.fromisoformat(result.data)  # type: ignore[arg-type]

            # Notification should have been received at least 1.5s before tool finished
            # (proves background flusher sent it, not final flush)
            notifications = recording_message_handler.get_notifications(
                "notifications/tools/list_changed"
            )
            assert len(notifications) == 1
            gap = tool_finished - notifications[0].timestamp
            assert gap >= timedelta(seconds=1.5)

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
