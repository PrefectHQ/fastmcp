"""Tests for session-specific visibility control via Context."""

from dataclasses import dataclass, field
from datetime import datetime

import anyio
import mcp.types

from fastmcp.client.messages import MessageHandler
from fastmcp.server.context import Context
from fastmcp.server.server import FastMCP


@dataclass
class NotificationRecording:
    """Record of a notification that was received."""

    method: str
    notification: mcp.types.ServerNotification
    timestamp: datetime = field(default_factory=datetime.now)


class RecordingMessageHandler(MessageHandler):
    """A message handler that records all notifications."""

    def __init__(self):
        super().__init__()
        self.notifications: list[NotificationRecording] = []

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

    def reset(self):
        """Clear all recorded notifications."""
        self.notifications.clear()


class TestSessionVisibility:
    """Test session-specific visibility control via Context."""

    async def test_enable_components_stores_rule_dict(self):
        """Test that enable_components stores a rule dict in session state."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            # Check that the rule was stored
            rules = await ctx._get_visibility_rules()
            assert len(rules) == 1
            assert rules[0]["enabled"] is True
            assert rules[0]["tags"] == ["finance"]
            return "activated"

        async with Client(mcp) as client:
            result = await client.call_tool("activate_finance", {})
            assert result.data == "activated"

    async def test_disable_components_stores_rule_dict(self):
        """Test that disable_components stores a rule dict in session state."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"internal"})
        def internal_tool() -> str:
            return "internal"

        @mcp.tool
        async def deactivate_internal(ctx: Context) -> str:
            await ctx.disable_components(tags={"internal"})
            # Check that the rule was stored
            rules = await ctx._get_visibility_rules()
            assert len(rules) == 1
            assert rules[0]["enabled"] is False
            assert rules[0]["tags"] == ["internal"]
            return "deactivated"

        async with Client(mcp) as client:
            result = await client.call_tool("deactivate_internal", {})
            assert result.data == "deactivated"

    async def test_session_rules_override_global_disables(self):
        """Test that session enable rules override global disable transforms."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        # Globally disable finance tools
        mcp.disable(tags={"finance"})

        async with Client(mcp) as client:
            # Before activation, finance tool should not be visible
            tools_before = await client.list_tools()
            assert not any(t.name == "finance_tool" for t in tools_before)

            # Activate finance for this session
            await client.call_tool("activate_finance", {})

            # After activation, finance tool should be visible in this session
            tools_after = await client.list_tools()
            assert any(t.name == "finance_tool" for t in tools_after)

    async def test_rules_persist_across_requests(self):
        """Test that session rules persist across multiple requests."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        @mcp.tool
        async def check_rules(ctx: Context) -> int:
            rules = await ctx._get_visibility_rules()
            return len(rules)

        # Globally disable finance tools
        mcp.disable(tags={"finance"})

        async with Client(mcp) as client:
            # Activate finance
            await client.call_tool("activate_finance", {})

            # In a subsequent request, rules should still be there
            result = await client.call_tool("check_rules", {})
            assert result.data == 1

            # And finance tool should still be visible
            tools = await client.list_tools()
            assert any(t.name == "finance_tool" for t in tools)

    async def test_rules_isolated_between_sessions(self):
        """Test that session rules are isolated between different sessions."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        # Globally disable finance tools
        mcp.disable(tags={"finance"})

        # Session A activates finance
        async with Client(mcp) as client_a:
            await client_a.call_tool("activate_finance", {})
            tools_a = await client_a.list_tools()
            assert any(t.name == "finance_tool" for t in tools_a)

        # Session B should not see finance tool (different session)
        async with Client(mcp) as client_b:
            tools_b = await client_b.list_tools()
            assert not any(t.name == "finance_tool" for t in tools_b)

    async def test_version_spec_serialization(self):
        """Test that VersionSpec is serialized/deserialized correctly."""
        from fastmcp import Client
        from fastmcp.utilities.versions import VersionSpec

        mcp = FastMCP("test")

        @mcp.tool(version="1.0.0")
        def old_tool() -> str:
            return "old"

        @mcp.tool(version="2.0.0")
        def new_tool() -> str:
            return "new"

        @mcp.tool
        async def enable_v2_only(ctx: Context) -> str:
            await ctx.enable_components(version=VersionSpec(gte="2.0.0"))
            # Check serialization - version is stored as a dict
            rules = await ctx._get_visibility_rules()
            assert rules[0]["version"]["gte"] == "2.0.0"
            assert rules[0]["version"]["lt"] is None
            assert rules[0]["version"]["eq"] is None
            return "enabled"

        # Globally disable all versioned tools
        mcp.disable(names={"old_tool", "new_tool"})

        async with Client(mcp) as client:
            # Enable v2 tools
            await client.call_tool("enable_v2_only", {})

            # Should see new_tool (v2.0.0) but not old_tool (v1.0.0)
            tools = await client.list_tools()
            assert any(t.name == "new_tool" for t in tools)
            assert not any(t.name == "old_tool" for t in tools)

    async def test_clear_visibility_rules(self):
        """Test that reset_visibility removes all session rules."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        @mcp.tool
        async def clear_rules(ctx: Context) -> str:
            await ctx.reset_visibility()
            rules = await ctx._get_visibility_rules()
            assert len(rules) == 0
            return "cleared"

        # Globally disable finance tools
        mcp.disable(tags={"finance"})

        async with Client(mcp) as client:
            # Activate finance
            await client.call_tool("activate_finance", {})
            tools_after_activate = await client.list_tools()
            assert any(t.name == "finance_tool" for t in tools_after_activate)

            # Clear rules
            await client.call_tool("clear_rules", {})

            # Finance tool should no longer be visible (back to global disable)
            tools_after_clear = await client.list_tools()
            assert not any(t.name == "finance_tool" for t in tools_after_clear)

    async def test_multiple_rules_accumulate(self):
        """Test that multiple enable/disable calls accumulate rules."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool(tags={"admin"})
        def admin_tool() -> str:
            return "admin"

        @mcp.tool
        async def activate_multiple(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            await ctx.enable_components(tags={"admin"})
            rules = await ctx._get_visibility_rules()
            assert len(rules) == 2
            return "activated"

        # Globally disable finance and admin tools
        mcp.disable(tags={"finance", "admin"})

        async with Client(mcp) as client:
            # Activate both
            await client.call_tool("activate_multiple", {})

            # Both should be visible
            tools = await client.list_tools()
            assert any(t.name == "finance_tool" for t in tools)
            assert any(t.name == "admin_tool" for t in tools)

    async def test_later_rules_override_earlier_rules(self):
        """Test that later session rules override earlier ones (mark semantics)."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"test"})
        def test_tool() -> str:
            return "test"

        @mcp.tool
        async def toggle_test(ctx: Context) -> str:
            # First enable, then disable
            await ctx.enable_components(tags={"test"})
            await ctx.disable_components(tags={"test"})
            return "toggled"

        async with Client(mcp) as client:
            # Toggle (enable then disable)
            await client.call_tool("toggle_test", {})

            # The disable should win (later mark overrides earlier)
            tools = await client.list_tools()
            assert not any(t.name == "test_tool" for t in tools)

    async def test_session_transforms_apply_to_resources(self):
        """Test that session transforms apply to resources too."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.resource("resource://finance", tags={"finance"})
        def finance_resource() -> str:
            return "finance data"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        # Globally disable finance resources
        mcp.disable(tags={"finance"})

        async with Client(mcp) as client:
            # Before activation, finance resource should not be visible
            resources_before = await client.list_resources()
            assert not any(str(r.uri) == "resource://finance" for r in resources_before)

            # Activate finance for this session
            await client.call_tool("activate_finance", {})

            # After activation, finance resource should be visible
            resources_after = await client.list_resources()
            assert any(str(r.uri) == "resource://finance" for r in resources_after)

    async def test_session_transforms_apply_to_prompts(self):
        """Test that session transforms apply to prompts too."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.prompt(tags={"finance"})
        def finance_prompt() -> str:
            return "finance prompt"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        # Globally disable finance prompts
        mcp.disable(tags={"finance"})

        async with Client(mcp) as client:
            # Before activation, finance prompt should not be visible
            prompts_before = await client.list_prompts()
            assert not any(p.name == "finance_prompt" for p in prompts_before)

            # Activate finance for this session
            await client.call_tool("activate_finance", {})

            # After activation, finance prompt should be visible
            prompts_after = await client.list_prompts()
            assert any(p.name == "finance_prompt" for p in prompts_after)


class TestSessionVisibilityNotifications:
    """Test that notifications are sent when session visibility changes."""

    async def test_enable_components_sends_notifications(self):
        """Test that enable_components sends all three notification types."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool
        async def activate(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        handler = RecordingMessageHandler()
        async with Client(mcp, message_handler=handler) as client:
            handler.reset()
            await client.call_tool("activate", {})

            # Should receive all three notifications
            tool_notifications = handler.get_notifications(
                "notifications/tools/list_changed"
            )
            resource_notifications = handler.get_notifications(
                "notifications/resources/list_changed"
            )
            prompt_notifications = handler.get_notifications(
                "notifications/prompts/list_changed"
            )
            assert len(tool_notifications) == 1
            assert len(resource_notifications) == 1
            assert len(prompt_notifications) == 1

    async def test_disable_components_sends_notifications(self):
        """Test that disable_components sends all three notification types."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool
        async def deactivate(ctx: Context) -> str:
            await ctx.disable_components(tags={"finance"})
            return "deactivated"

        handler = RecordingMessageHandler()
        async with Client(mcp, message_handler=handler) as client:
            handler.reset()
            await client.call_tool("deactivate", {})

            # Should receive all three notifications
            assert (
                len(handler.get_notifications("notifications/tools/list_changed")) == 1
            )
            assert (
                len(handler.get_notifications("notifications/resources/list_changed"))
                == 1
            )
            assert (
                len(handler.get_notifications("notifications/prompts/list_changed"))
                == 1
            )

    async def test_clear_visibility_rules_sends_notifications(self):
        """Test that reset_visibility sends notifications."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool
        async def clear(ctx: Context) -> str:
            await ctx.reset_visibility()
            return "cleared"

        handler = RecordingMessageHandler()
        async with Client(mcp, message_handler=handler) as client:
            handler.reset()
            await client.call_tool("clear", {})

            # Should receive all three notifications
            assert (
                len(handler.get_notifications("notifications/tools/list_changed")) == 1
            )
            assert (
                len(handler.get_notifications("notifications/resources/list_changed"))
                == 1
            )
            assert (
                len(handler.get_notifications("notifications/prompts/list_changed"))
                == 1
            )

    async def test_components_hint_limits_notifications(self):
        """Test that the components hint limits which notifications are sent."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool
        async def activate_tools_only(ctx: Context) -> str:
            # Only specify tool components - should only send tool notification
            await ctx.enable_components(tags={"finance"}, components={"tool"})
            return "activated"

        handler = RecordingMessageHandler()
        async with Client(mcp, message_handler=handler) as client:
            handler.reset()
            await client.call_tool("activate_tools_only", {})

            # Should only receive tool notification
            assert (
                len(handler.get_notifications("notifications/tools/list_changed")) == 1
            )
            assert (
                len(handler.get_notifications("notifications/resources/list_changed"))
                == 0
            )
            assert (
                len(handler.get_notifications("notifications/prompts/list_changed"))
                == 0
            )


class TestConcurrentSessionIsolation:
    """Test that concurrent sessions don't leak visibility transforms."""

    async def test_concurrent_sessions_isolated(self):
        """Test that two concurrent clients don't leak session transforms."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"finance"})
        def finance_tool() -> str:
            return "finance"

        @mcp.tool
        async def activate_finance(ctx: Context) -> str:
            await ctx.enable_components(tags={"finance"})
            return "activated"

        # Globally disable finance tools
        mcp.disable(tags={"finance"})

        # Track what each session sees
        session_a_sees_finance = False
        session_b_sees_finance = False
        ready_event = anyio.Event()

        async def session_a():
            nonlocal session_a_sees_finance
            async with Client(mcp) as client:
                # Activate finance for this session
                await client.call_tool("activate_finance", {})

                # Signal that session A has activated
                ready_event.set()

                # Check that session A sees finance tool
                tools = await client.list_tools()
                session_a_sees_finance = any(t.name == "finance_tool" for t in tools)

                # Keep session A alive while session B checks
                await anyio.sleep(0.2)

        async def session_b():
            nonlocal session_b_sees_finance
            # Wait for session A to activate
            await ready_event.wait()

            async with Client(mcp) as client:
                # Session B should NOT see finance tool
                tools = await client.list_tools()
                session_b_sees_finance = any(t.name == "finance_tool" for t in tools)

        async with anyio.create_task_group() as tg:
            tg.start_soon(session_a)
            tg.start_soon(session_b)

        # Session A should see finance, session B should not
        assert session_a_sees_finance is True, "Session A should see finance tool"
        assert session_b_sees_finance is False, "Session B should NOT see finance tool"

    async def test_many_concurrent_sessions_isolated(self):
        """Test that many concurrent sessions remain properly isolated."""
        from fastmcp import Client

        mcp = FastMCP("test")

        @mcp.tool(tags={"premium"})
        def premium_tool() -> str:
            return "premium"

        @mcp.tool
        async def activate_premium(ctx: Context) -> str:
            await ctx.enable_components(tags={"premium"})
            return "activated"

        # Globally disable premium tools
        mcp.disable(tags={"premium"})

        results: dict[str, bool] = {}

        async def activated_session(session_id: str):
            async with Client(mcp) as client:
                await client.call_tool("activate_premium", {})
                tools = await client.list_tools()
                results[session_id] = any(t.name == "premium_tool" for t in tools)

        async def non_activated_session(session_id: str):
            async with Client(mcp) as client:
                tools = await client.list_tools()
                results[session_id] = any(t.name == "premium_tool" for t in tools)

        async with anyio.create_task_group() as tg:
            # Start 5 activated sessions
            for i in range(5):
                tg.start_soon(activated_session, f"activated_{i}")
            # Start 5 non-activated sessions
            for i in range(5):
                tg.start_soon(non_activated_session, f"non_activated_{i}")

        # All activated sessions should see premium tool
        for i in range(5):
            assert results[f"activated_{i}"] is True, (
                f"Activated session {i} should see premium tool"
            )

        # All non-activated sessions should NOT see premium tool
        for i in range(5):
            assert results[f"non_activated_{i}"] is False, (
                f"Non-activated session {i} should NOT see premium tool"
            )
