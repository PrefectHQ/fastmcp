"""Tests for resource subscription support (resources/subscribe / resources/unsubscribe).

Covers:
- ResourceSubscriptionRegistry data-structure correctness
- subscribe/unsubscribe/remove_session semantics
- Weak-reference eviction of dead sessions
- subscribe=True advertised in server capabilities
- subscribe/unsubscribe round-trip via FastMCP Client
- notify_resource_updated() dispatches notifications only to subscribers
"""

from __future__ import annotations

import gc
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.subscriptions import ResourceSubscriptionRegistry, get_registry

# ---------------------------------------------------------------------------
# Registry unit tests
# ---------------------------------------------------------------------------


class TestResourceSubscriptionRegistry:
    @pytest.fixture
    def registry(self) -> ResourceSubscriptionRegistry:
        return ResourceSubscriptionRegistry()

    def _make_session(self) -> MagicMock:
        session = MagicMock()
        session.send_resource_updated = AsyncMock()
        return session

    async def test_subscribe_and_get_subscribers(self, registry):
        session = self._make_session()
        await registry.subscribe("resource://foo", session)
        assert session in registry.get_subscribers("resource://foo")

    async def test_subscribe_idempotent(self, registry):
        session = self._make_session()
        await registry.subscribe("resource://foo", session)
        await registry.subscribe("resource://foo", session)
        # Only one entry for the same session
        assert registry.get_subscribers("resource://foo").count(session) == 1

    async def test_unsubscribe_removes_session(self, registry):
        session = self._make_session()
        await registry.subscribe("resource://foo", session)
        await registry.unsubscribe("resource://foo", session)
        assert session not in registry.get_subscribers("resource://foo")

    async def test_unsubscribe_no_op_when_not_subscribed(self, registry):
        session = self._make_session()
        # Should not raise
        await registry.unsubscribe("resource://foo", session)

    async def test_unsubscribe_cleans_up_empty_uri(self, registry):
        session = self._make_session()
        await registry.subscribe("resource://foo", session)
        await registry.unsubscribe("resource://foo", session)
        assert "resource://foo" not in registry._subscriptions

    async def test_multiple_sessions_same_uri(self, registry):
        s1 = self._make_session()
        s2 = self._make_session()
        await registry.subscribe("resource://bar", s1)
        await registry.subscribe("resource://bar", s2)
        subscribers = registry.get_subscribers("resource://bar")
        assert s1 in subscribers
        assert s2 in subscribers

    async def test_remove_session_clears_all_uris(self, registry):
        session = self._make_session()
        await registry.subscribe("resource://a", session)
        await registry.subscribe("resource://b", session)
        await registry.remove_session(session)
        assert session not in registry.get_subscribers("resource://a")
        assert session not in registry.get_subscribers("resource://b")

    async def test_weakref_dead_session_not_returned(self, registry):
        """A session that has been garbage-collected must not appear in subscribers."""
        session = self._make_session()
        await registry.subscribe("resource://foo", session)
        # Delete the strong reference and force GC
        del session
        gc.collect()
        # get_subscribers should return only live sessions
        live = registry.get_subscribers("resource://foo")
        assert len(live) == 0

    async def test_get_registry_returns_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2


# ---------------------------------------------------------------------------
# Server capabilities
# ---------------------------------------------------------------------------


class TestSubscribeCapability:
    async def test_capabilities_advertise_subscribe_true(self):
        """Server must advertise subscribe=True in ServerCapabilities."""
        mcp = FastMCP()

        @mcp.resource("resource://test")
        def my_resource() -> str:
            return "hello"

        async with Client(mcp) as client:
            result = client.initialize_result
            assert result is not None
            caps = result.capabilities
            assert caps.resources is not None
            assert caps.resources.subscribe is True


# ---------------------------------------------------------------------------
# Subscribe / unsubscribe round-trip
# ---------------------------------------------------------------------------


class TestSubscribeRoundTrip:
    async def test_subscribe_registers_in_registry(self):
        """resources/subscribe must add the session to the registry."""
        mcp = FastMCP()

        @mcp.resource("resource://live")
        def live_resource() -> str:
            return "data"

        registry = get_registry()
        # Clear any state from previous tests
        registry._subscriptions.clear()

        async with Client(mcp) as client:
            await client.session.subscribe_resource("resource://live")
            subscribers = registry.get_subscribers("resource://live")
            assert len(subscribers) == 1

    async def test_unsubscribe_removes_from_registry(self):
        """resources/unsubscribe must remove the session from the registry."""
        mcp = FastMCP()

        @mcp.resource("resource://live2")
        def live_resource2() -> str:
            return "data"

        registry = get_registry()
        registry._subscriptions.clear()

        async with Client(mcp) as client:
            await client.session.subscribe_resource("resource://live2")
            assert len(registry.get_subscribers("resource://live2")) == 1

            await client.session.unsubscribe_resource("resource://live2")
            assert len(registry.get_subscribers("resource://live2")) == 0

    async def test_session_cleanup_on_disconnect(self):
        """Registry must be empty for the session after client disconnects."""
        mcp = FastMCP()

        @mcp.resource("resource://cleanup")
        def cleanup_resource() -> str:
            return "data"

        registry = get_registry()
        registry._subscriptions.clear()

        async with Client(mcp) as client:
            await client.session.subscribe_resource("resource://cleanup")
            assert len(registry.get_subscribers("resource://cleanup")) == 1

        # After exiting the context manager the session is gone
        assert len(registry.get_subscribers("resource://cleanup")) == 0


# ---------------------------------------------------------------------------
# notify_resource_updated
# ---------------------------------------------------------------------------


class TestNotifyResourceUpdated:
    async def test_notify_sends_to_subscribers(self):
        """notify_resource_updated() must send to subscribed sessions."""
        from fastmcp.server.context import Context

        mcp = FastMCP()
        registry = get_registry()
        registry._subscriptions.clear()

        mock_session = MagicMock()
        mock_session.send_resource_updated = AsyncMock()

        await registry.subscribe("resource://notify-test", mock_session)

        ctx = Context(fastmcp=mcp)
        await ctx.notify_resource_updated("resource://notify-test")

        mock_session.send_resource_updated.assert_called_once()
        call_arg = mock_session.send_resource_updated.call_args[0][0]
        assert str(call_arg) == "resource://notify-test"

    async def test_notify_skips_unsubscribed_sessions(self):
        """notify_resource_updated() must not send to unrelated sessions."""
        from fastmcp.server.context import Context

        mcp = FastMCP()
        registry = get_registry()
        registry._subscriptions.clear()

        mock_session = MagicMock()
        mock_session.send_resource_updated = AsyncMock()

        await registry.subscribe("resource://other", mock_session)

        ctx = Context(fastmcp=mcp)
        await ctx.notify_resource_updated("resource://notify-no-match")

        mock_session.send_resource_updated.assert_not_called()

    async def test_notify_tolerates_send_failure(self):
        """notify_resource_updated() must not raise if a session send fails."""
        from fastmcp.server.context import Context

        mcp = FastMCP()
        registry = get_registry()
        registry._subscriptions.clear()

        mock_session = MagicMock()
        mock_session.send_resource_updated = AsyncMock(side_effect=RuntimeError("gone"))

        await registry.subscribe("resource://err-test", mock_session)

        ctx = Context(fastmcp=mcp)
        # Should not raise
        await ctx.notify_resource_updated("resource://err-test")
