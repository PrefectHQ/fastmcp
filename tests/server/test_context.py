from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from mcp.types import ModelPreferences

from fastmcp.server.context import (
    Context,
    _parse_model_preferences,
    reset_transport,
    set_transport,
)
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

        assert context.session_id == "test-session-123"

        request_ctx.reset(token)

    def test_session_id_without_http_headers(self, context):
        """Test that session_id returns a UUID string when no HTTP headers are available."""
        import uuid

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

        assert uuid.UUID(context.session_id)

        request_ctx.reset(token)


class TestContextState:
    """Test suite for Context state functionality."""

    async def test_context_state(self):
        """Test that state modifications in child contexts don't affect parent."""
        mock_fastmcp = MagicMock()

        async with Context(fastmcp=mock_fastmcp) as context:
            assert context.get_state("test1") is None
            assert context.get_state("test2") is None
            context.set_state("test1", "value")
            context.set_state("test2", 2)
            assert context.get_state("test1") == "value"
            assert context.get_state("test2") == 2
            context.set_state("test1", "new_value")
            assert context.get_state("test1") == "new_value"

    async def test_context_state_inheritance(self):
        """Test that child contexts inherit parent state."""
        mock_fastmcp = MagicMock()

        async with Context(fastmcp=mock_fastmcp) as context1:
            context1.set_state("key1", "key1-context1")
            context1.set_state("key2", "key2-context1")
            async with Context(fastmcp=mock_fastmcp) as context2:
                # Override one key
                context2.set_state("key1", "key1-context2")
                assert context2.get_state("key1") == "key1-context2"
                assert context1.get_state("key1") == "key1-context1"
                assert context2.get_state("key2") == "key2-context1"

                async with Context(fastmcp=mock_fastmcp) as context3:
                    # Verify state was inherited
                    assert context3.get_state("key1") == "key1-context2"
                    assert context3.get_state("key2") == "key2-context1"

                    # Add a new key and verify parents were not affected
                    context3.set_state("key-context3-only", 1)
                    assert context1.get_state("key-context3-only") is None
                    assert context2.get_state("key-context3-only") is None
                    assert context3.get_state("key-context3-only") == 1

            assert context1.get_state("key1") == "key1-context1"
            assert context1.get_state("key-context3-only") is None


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
