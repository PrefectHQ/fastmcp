from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from mcp.types import ModelPreferences

from fastmcp.server.context import (
    Context,
    reset_transport,
    set_transport,
)
from fastmcp.server.sampling.run import _parse_model_preferences
from fastmcp.server.server import FastMCP


@pytest.fixture
def context():
    return Context(fastmcp=FastMCP())


class TestParseModelPreferences:
    def test_parse_model_preferences_string(self, context):
        mp = _parse_model_preferences("claude-haiku-4-5")
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert mp.hints[0].name == "claude-haiku-4-5"

    def test_parse_model_preferences_list(self, context):
        mp = _parse_model_preferences(["claude-haiku-4-5", "claude"])
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert [h.name for h in mp.hints] == ["claude-haiku-4-5", "claude"]

    def test_parse_model_preferences_object(self, context):
        obj = ModelPreferences(hints=[])
        assert _parse_model_preferences(obj) is obj

    def test_parse_model_preferences_invalid_type(self, context):
        with pytest.raises(ValueError):
            _parse_model_preferences(model_preferences=123)  # pyright: ignore[reportArgumentType] # type: ignore[invalid-argument-type]


class TestSessionId:
    def test_session_id_with_http_headers(self, context):
        """Test that session_id returns the value from mcp-session-id header."""
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        mock_headers = {"mcp-session-id": "test-session-123"}

        token = request_ctx.set(
            RequestContext(
                request_id=0,
                meta=None,
                session=MagicMock(wraps={}),
                lifespan_context=MagicMock(),
                request=MagicMock(headers=mock_headers),
            )
        )

        try:
            assert context.session_id == "test-session-123"
        finally:
            request_ctx.reset(token)

    def test_session_id_without_http_headers(self, context):
        """Test that session_id returns a UUID when no HTTP headers are available.

        For STDIO/SSE/in-memory transports, we generate a UUID and cache it
        on the session for consistency with state operations.
        """
        import uuid

        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        mock_session = MagicMock(wraps={})
        token = request_ctx.set(
            RequestContext(
                request_id=0,
                meta=None,
                session=mock_session,
                lifespan_context=MagicMock(),
            )
        )

        try:
            # session_id should be a valid UUID for non-HTTP transports
            session_id = context.session_id
            assert uuid.UUID(session_id)  # Valid UUID format
            # Should be cached on session
            assert mock_session._fastmcp_state_prefix == session_id
        finally:
            request_ctx.reset(token)


class TestContextState:
    """Test suite for Context state functionality."""

    async def test_context_state_basic(self):
        """Test basic get/set/delete state operations."""
        server = FastMCP("test")
        mock_session = MagicMock()  # Use same session for consistent id()

        async with Context(fastmcp=server, session=mock_session) as context:
            # Initially empty
            assert await context.get_state("test1") is None
            assert await context.get_state("test2") is None

            # Set values
            await context.set_state("test1", "value")
            await context.set_state("test2", 2)

            # Retrieve values
            assert await context.get_state("test1") == "value"
            assert await context.get_state("test2") == 2

            # Update value
            await context.set_state("test1", "new_value")
            assert await context.get_state("test1") == "new_value"

            # Delete value
            await context.delete_state("test1")
            assert await context.get_state("test1") is None

    async def test_context_state_session_isolation(self):
        """Test that different sessions have isolated state."""
        server = FastMCP("test")
        session_a = MagicMock()
        session_b = MagicMock()

        async with Context(fastmcp=server, session=session_a) as context1:
            await context1.set_state("key", "value-from-A")

        async with Context(fastmcp=server, session=session_b) as context2:
            # Session B should not see session A's state
            assert await context2.get_state("key") is None
            await context2.set_state("key", "value-from-B")
            assert await context2.get_state("key") == "value-from-B"

        # Verify session A's state is still intact
        async with Context(fastmcp=server, session=session_a) as context3:
            assert await context3.get_state("key") == "value-from-A"

    async def test_context_state_persists_across_requests(self):
        """Test that state persists across multiple context instances (requests)."""
        server = FastMCP("test")
        mock_session = MagicMock()  # Same session = same id()

        # First request sets state
        async with Context(fastmcp=server, session=mock_session) as context1:
            await context1.set_state("counter", 1)

        # Second request in same session sees the state
        async with Context(fastmcp=server, session=mock_session) as context2:
            counter = await context2.get_state("counter")
            assert counter == 1
            await context2.set_state("counter", counter + 1)

        # Third request sees updated state
        async with Context(fastmcp=server, session=mock_session) as context3:
            assert await context3.get_state("counter") == 2

    async def test_context_state_nested_contexts_share_state(self):
        """Test that nested contexts within the same session share state."""
        server = FastMCP("test")
        mock_session = MagicMock()

        async with Context(fastmcp=server, session=mock_session) as context1:
            await context1.set_state("key", "outer-value")

            async with Context(fastmcp=server, session=mock_session) as context2:
                # Nested context sees same state (same session)
                assert await context2.get_state("key") == "outer-value"

                # Nested context can modify shared state
                await context2.set_state("key", "inner-value")

            # Outer context sees the modification
            assert await context1.get_state("key") == "inner-value"

    async def test_two_clients_same_key_isolated_by_session(self):
        """Test that two different clients can store the same key independently.

        Each client gets an auto-generated session ID, and their state is isolated.
        """
        import json

        from fastmcp import Client

        server = FastMCP("test")
        stored_session_ids: list[str] = []

        @server.tool
        async def store_and_read(value: str, ctx: Context) -> dict:
            """Store a value and return all state info."""
            stored_session_ids.append(ctx.session_id)
            existing = await ctx.get_state("shared_key")
            await ctx.set_state("shared_key", value)
            new_value = await ctx.get_state("shared_key")
            return {
                "session_id": ctx.session_id,
                "existing_value": existing,
                "new_value": new_value,
            }

        # Client 1 stores "value-from-client-1"
        async with Client(server) as client1:
            result1 = await client1.call_tool(
                "store_and_read", {"value": "value-from-client-1"}
            )
            data1 = json.loads(result1.content[0].text)
            assert data1["existing_value"] is None  # First write
            assert data1["new_value"] == "value-from-client-1"
            session_id_1 = data1["session_id"]

        # Client 2 stores "value-from-client-2" with the SAME key
        async with Client(server) as client2:
            result2 = await client2.call_tool(
                "store_and_read", {"value": "value-from-client-2"}
            )
            data2 = json.loads(result2.content[0].text)
            # Client 2 should NOT see client 1's value (different session)
            assert data2["existing_value"] is None
            assert data2["new_value"] == "value-from-client-2"
            session_id_2 = data2["session_id"]

        # Verify session IDs were auto-generated and are different
        assert session_id_1 is not None
        assert session_id_2 is not None
        assert session_id_1 != session_id_2

        # Client 1 reconnects and should still see their value
        async with Client(server) as client1_again:
            # But this is a NEW session (new connection = new session ID)
            result3 = await client1_again.call_tool(
                "store_and_read", {"value": "value-from-client-1-again"}
            )
            data3 = json.loads(result3.content[0].text)
            # New session, so existing value is None
            assert data3["existing_value"] is None
            assert data3["session_id"] != session_id_1  # Different session


class TestContextMeta:
    """Test suite for Context meta functionality."""

    def test_request_context_meta_access(self, context):
        """Test that meta can be accessed from request context."""
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        # Create a mock meta object with attributes
        class MockMeta:
            def __init__(self):
                self.user_id = "user-123"
                self.trace_id = "trace-456"
                self.custom_field = "custom-value"

        mock_meta = MockMeta()

        token = request_ctx.set(
            RequestContext(
                request_id=0,
                meta=cast(Any, mock_meta),  # Mock object for testing
                session=MagicMock(wraps={}),
                lifespan_context=MagicMock(),
            )
        )

        # Access meta through context
        retrieved_meta = context.request_context.meta
        assert retrieved_meta is not None
        assert retrieved_meta.user_id == "user-123"
        assert retrieved_meta.trace_id == "trace-456"
        assert retrieved_meta.custom_field == "custom-value"

        request_ctx.reset(token)

    def test_request_context_meta_none(self, context):
        """Test that context handles None meta gracefully."""
        from mcp.server.lowlevel.server import request_ctx
        from mcp.shared.context import RequestContext

        token = request_ctx.set(
            RequestContext(
                request_id=0,
                meta=None,
                session=MagicMock(wraps={}),
                lifespan_context=MagicMock(),
            )
        )

        # Access meta through context
        retrieved_meta = context.request_context.meta
        assert retrieved_meta is None

        request_ctx.reset(token)


class TestTransport:
    """Test suite for Context transport property."""

    def test_transport_returns_none_outside_server_context(self, context):
        """Test that transport returns None when not in a server context."""
        assert context.transport is None

    def test_transport_returns_stdio(self, context):
        """Test that transport returns 'stdio' when set."""
        token = set_transport("stdio")
        try:
            assert context.transport == "stdio"
        finally:
            reset_transport(token)

    def test_transport_returns_sse(self, context):
        """Test that transport returns 'sse' when set."""
        token = set_transport("sse")
        try:
            assert context.transport == "sse"
        finally:
            reset_transport(token)

    def test_transport_returns_streamable_http(self, context):
        """Test that transport returns 'streamable-http' when set."""
        token = set_transport("streamable-http")
        try:
            assert context.transport == "streamable-http"
        finally:
            reset_transport(token)

    def test_transport_reset(self, context):
        """Test that transport resets correctly."""
        assert context.transport is None
        token = set_transport("stdio")
        assert context.transport == "stdio"
        reset_transport(token)
        assert context.transport is None


class TestTransportIntegration:
    """Integration tests for transport property with actual server/client."""

    async def test_transport_in_tool_via_client(self):
        """Test that transport is accessible from within a tool via Client."""
        from fastmcp import Client

        mcp = FastMCP("test")
        observed_transport = None

        @mcp.tool
        def get_transport(ctx: Context) -> str:
            nonlocal observed_transport
            observed_transport = ctx.transport
            return observed_transport or "none"

        # Client uses in-memory transport which doesn't set transport type
        # so we expect None here (the transport is only set by run_* methods)
        async with Client(mcp) as client:
            result = await client.call_tool("get_transport", {})
            assert observed_transport is None
            assert result.data == "none"

    async def test_transport_set_manually_is_visible_in_tool(self):
        """Test that manually set transport is visible from within a tool."""
        from fastmcp import Client

        mcp = FastMCP("test")
        observed_transport = None

        @mcp.tool
        def get_transport(ctx: Context) -> str:
            nonlocal observed_transport
            observed_transport = ctx.transport
            return observed_transport or "none"

        # Manually set transport before running
        token = set_transport("stdio")
        try:
            async with Client(mcp) as client:
                result = await client.call_tool("get_transport", {})
                assert observed_transport == "stdio"
                assert result.data == "stdio"
        finally:
            reset_transport(token)

    async def test_transport_set_via_http_middleware(self):
        """Test that transport is set per-request via HTTP middleware."""
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from fastmcp.utilities.tests import run_server_async

        mcp = FastMCP("test")
        observed_transport = None

        @mcp.tool
        def get_transport(ctx: Context) -> str:
            nonlocal observed_transport
            observed_transport = ctx.transport
            return observed_transport or "none"

        async with run_server_async(mcp, transport="streamable-http") as url:
            transport = StreamableHttpTransport(url=url)
            async with Client(transport=transport) as client:
                result = await client.call_tool("get_transport", {})
                assert observed_transport == "streamable-http"
                assert result.data == "streamable-http"
