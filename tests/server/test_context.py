import warnings
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request

from fastmcp.server.context import Context


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
