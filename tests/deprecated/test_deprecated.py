"""Tests for deprecated functionality."""

import warnings
from unittest.mock import AsyncMock, patch

import pytest
from starlette.applications import Starlette

from fastmcp import Client, FastMCP


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
        assert call_kwargs.get("transport") == "streamable-http"


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


def test_mount_tool_separator_deprecation_warning():
    """Test that using tool_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The tool_separator parameter is deprecated and will be removed in a future version",
    ):
        main_app.mount("sub", sub_app, tool_separator="-")

    # Verify the separator is ignored and the default is used
    @sub_app.tool()
    def test_tool():
        return "test"

    mounted_server = main_app._mounted_servers["sub"]
    assert mounted_server.match_tool("sub_test_tool")
    assert not mounted_server.match_tool("sub-test_tool")


def test_mount_resource_separator_deprecation_warning():
    """Test that using resource_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The resource_separator parameter is deprecated and ignored",
    ):
        main_app.mount("sub", sub_app, resource_separator="+")


def test_mount_prompt_separator_deprecation_warning():
    """Test that using prompt_separator in mount() raises a deprecation warning."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The prompt_separator parameter is deprecated and will be removed in a future version",
    ):
        main_app.mount("sub", sub_app, prompt_separator="-")

    # Verify the separator is ignored and the default is used
    @sub_app.prompt()
    def test_prompt():
        return "test"

    mounted_server = main_app._mounted_servers["sub"]
    assert mounted_server.match_prompt("sub_test_prompt")
    assert not mounted_server.match_prompt("sub-test_prompt")


async def test_import_server_separator_deprecation_warnings():
    """Test that using separators in import_server() raises deprecation warnings."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    with pytest.warns(
        DeprecationWarning,
        match="The tool_separator parameter is deprecated and will be removed in a future version",
    ):
        await main_app.import_server("sub", sub_app, tool_separator="-")

    main_app = FastMCP("MainApp")
    with pytest.warns(
        DeprecationWarning,
        match="The resource_separator parameter is deprecated and ignored",
    ):
        await main_app.import_server("sub", sub_app, resource_separator="+")

    main_app = FastMCP("MainApp")
    with pytest.warns(
        DeprecationWarning,
        match="The prompt_separator parameter is deprecated and will be removed in a future version",
    ):
        await main_app.import_server("sub", sub_app, prompt_separator="-")
