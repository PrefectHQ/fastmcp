import warnings
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import ModelPreferences
from starlette.requests import Request

from fastmcp.server.context import Context
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
                assert "https://gofastmcp.com/patterns/http-requests" in str(
                    warning.message
                )


@pytest.fixture
def context():
    return Context(fastmcp=FastMCP())


class TestParseModelPreferences:
    def test_parse_model_preferences_string(self, context):
        mp = context._parse_model_preferences("claude-3-sonnet")
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert mp.hints[0].name == "claude-3-sonnet"

    def test_parse_model_preferences_list(self, context):
        mp = context._parse_model_preferences(["claude-3-sonnet", "claude"])
        assert isinstance(mp, ModelPreferences)
        assert mp.hints is not None
        assert [h.name for h in mp.hints] == ["claude-3-sonnet", "claude"]

    def test_parse_model_preferences_object(self, context):
        obj = ModelPreferences(hints=[])
        assert context._parse_model_preferences(obj) is obj

    def test_parse_model_preferences_invalid_type(self, context):
        with pytest.raises(ValueError):
            context._parse_model_preferences(123)
