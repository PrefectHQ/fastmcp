import warnings
from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette

from fastmcp import Client, FastMCP
from fastmcp.utilities.tests import temporary_settings

# reset deprecation warnings for this module
pytestmark = pytest.mark.filterwarnings("default::DeprecationWarning")


class TestDeprecationWarningsSetting:
    def test_deprecation_warnings_setting_true(self):
        with temporary_settings(deprecation_warnings=True):
            with pytest.warns(DeprecationWarning) as recorded_warnings:
                # will warn once for providing deprecated arg
                mcp = FastMCP(host="1.2.3.4")
                # will warn once for accessing deprecated property
                mcp.settings

            assert len(recorded_warnings) == 2

    def test_deprecation_warnings_setting_false(self):
        with temporary_settings(deprecation_warnings=False):
            # will error if a warning is raised
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                # will warn once for providing deprecated arg
                mcp = FastMCP(host="1.2.3.4")
                # will warn once for accessing deprecated property
                mcp.settings


def test_sse_app_deprecation_warning():
    """Test that sse_app raises a deprecation warning."""
    server = FastMCP("TestServer")

    with pytest.warns(DeprecationWarning, match="The sse_app method is deprecated"):
        app = server.sse_app()
        assert isinstance(app, Starlette)


def test_streamable_http_app_deprecation_warning():
    """Test that streamable_http_app raises a deprecation warning."""
    server = FastMCP("TestServer")

    with pytest.warns(
        DeprecationWarning, match="The streamable_http_app method is deprecated"
    ):
        app = server.streamable_http_app()
        assert isinstance(app, Starlette)


async def test_run_sse_async_deprecation_warning():
    """Test that run_sse_async raises a deprecation warning."""
    server = FastMCP("TestServer")

    # Use patch to avoid actually running the server
    with patch.object(server, "run_http_async", new_callable=AsyncMock) as mock_run:
        with pytest.warns(
            DeprecationWarning, match="The run_sse_async method is deprecated"
        ):
            await server.run_sse_async()

        # Verify the mock was called with the right transport
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("transport") == "sse"


async def test_run_streamable_http_async_deprecation_warning():
    """Test that run_streamable_http_async raises a deprecation warning."""
    server = FastMCP("TestServer")

    # Use patch to avoid actually running the server
    with patch.object(server, "run_http_async", new_callable=AsyncMock) as mock_run:
        with pytest.warns(
            DeprecationWarning,
            match="The run_streamable_http_async method is deprecated",
        ):
            await server.run_streamable_http_async()

        # Verify the mock was called with the right transport
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("transport") == "http"


def test_http_app_with_sse_transport():
    """Test that http_app with SSE transport works (no warning)."""
    server = FastMCP("TestServer")

    # This should not raise a warning since we're using the new API
    with warnings.catch_warnings(record=True) as recorded_warnings:
        app = server.http_app(transport="sse")
        assert isinstance(app, Starlette)

        # Verify no deprecation warnings were raised for using transport parameter
        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0


def test_from_client_deprecation_warning():
    """Test that FastMCP.from_client raises a deprecation warning."""
    server = FastMCP("TestServer")
    with pytest.warns(DeprecationWarning, match="from_client"):
        FastMCP.from_client(Client(server))
