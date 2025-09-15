import warnings
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import ModelPreferences
from starlette.requests import Request

from fastmcp.server.context import Context, _parse_model_preferences
from fastmcp.server.server import FastMCP


class TestContextDeprecations:
    def test_get_http_request_deprecation_warning(self):
        """Test that using Context.get_http_request() raises a deprecation warning."""
        # Create a mock FastMCP instance
        mock_fastmcp = MagicMock()
        context = Context(fastmcp=mock_fastmcp)

        # Patch the dependency function to return a mock request
        mock_request = MagicMock(spec=Request)
        with patch(
            "fastmcp.server.dependencies.get_http_request", return_value=mock_request
        ):
            # Check that the deprecation warning is raised
            with pytest.warns(
                DeprecationWarning, match="Context.get_http_request\\(\\) is deprecated"
            ):
                request = context.get_http_request()

            # Verify the function still works and returns the request
            assert request is mock_request

    def test_get_http_request_deprecation_message(self):
        """Test that the deprecation warning has the correct message with guidance."""
        # Create a mock FastMCP instance
        mock_fastmcp = MagicMock()
        context = Context(fastmcp=mock_fastmcp)

        # Patch the dependency function to return a mock request
        mock_request = MagicMock(spec=Request)
        with patch(
            "fastmcp.server.dependencies.get_http_request", return_value=mock_request
        ):
            # Capture and check the specific warning message
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                context.get_http_request()

                assert len(w) == 1
                warning = w[0]
                assert issubclass(warning.category, DeprecationWarning)
                assert "Context.get_http_request() is deprecated" in str(
                    warning.message
                )
                assert (
                    "Use get_http_request() from fastmcp.server.dependencies instead"
                    in str(warning.message)
                )
                assert "https://gofastmcp.com/servers/context#http-requests" in str(
                    warning.message
                )


@pytest.fixture
def context():
    return Context(fastmcp=FastMCP())


class TestParseModelPreferences:
    def test_parse_model_preferences_string(self, context):
        mp = _parse_model_preferences("claude-3-sonnet")
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert mp.hints[0].name == "claude-3-sonnet"

    def test_parse_model_preferences_list(self, context):
        mp = _parse_model_preferences(["claude-3-sonnet", "claude"])
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert [h.name for h in mp.hints] == ["claude-3-sonnet", "claude"]

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
            RequestContext(  # type: ignore[arg-type]
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
            RequestContext(  # type: ignore[arg-type]
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
