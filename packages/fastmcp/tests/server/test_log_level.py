"""Test log_level parameter support in FastMCP server."""

import asyncio
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP


class TestLogLevelParameter:
    """Test that log_level parameter is properly accepted by run methods."""

    async def test_run_stdio_accepts_log_level(self):
        """Test that run_stdio_async accepts log_level parameter."""
        server = FastMCP("TestServer")

        # Mock the stdio_server to avoid actual stdio operations
        with patch("fastmcp.server.mixins.transport.stdio_server") as mock_stdio:
            mock_stdio.return_value.__aenter__ = AsyncMock(
                return_value=(AsyncMock(), AsyncMock())
            )
            mock_stdio.return_value.__aexit__ = AsyncMock()

            # Mock the underlying MCP server run method
            with patch.object(server._mcp_server, "run", new_callable=AsyncMock):
                try:
                    # This should accept the log_level parameter without error
                    await asyncio.wait_for(
                        server.run_stdio_async(log_level="DEBUG", show_banner=False),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    pass  # Expected since we're mocking

    async def test_run_http_accepts_log_level(self):
        """Test that run_http_async accepts log_level parameter."""
        server = FastMCP("TestServer")

        # Mock uvicorn to avoid actual server start
        with patch(
            "fastmcp.server.mixins.transport.uvicorn.Server"
        ) as mock_server_class:
            mock_instance = mock_server_class.return_value
            mock_instance.serve = AsyncMock()

            # This should accept the log_level parameter without error
            await server.run_http_async(
                log_level="INFO", show_banner=False, host="127.0.0.1", port=8000
            )

            # Verify serve was called
            mock_instance.serve.assert_called_once()

    async def test_run_async_passes_log_level(self):
        """Test that run_async passes log_level to transport methods."""
        server = FastMCP("TestServer")

        # Test stdio transport
        with patch.object(
            server, "run_stdio_async", new_callable=AsyncMock
        ) as mock_stdio:
            await server.run_async(transport="stdio", log_level="WARNING")
            mock_stdio.assert_called_once_with(show_banner=True, log_level="WARNING")

        # Test http transport
        with patch.object(
            server, "run_http_async", new_callable=AsyncMock
        ) as mock_http:
            await server.run_async(transport="http", log_level="ERROR")
            mock_http.assert_called_once_with(
                transport="http", show_banner=True, log_level="ERROR"
            )

    def test_sync_run_accepts_log_level(self):
        """Test that the synchronous run method accepts log_level."""
        server = FastMCP("TestServer")

        with patch.object(server, "run_async", new_callable=AsyncMock):
            # Mock anyio.run to avoid actual async execution
            with patch("anyio.run") as mock_anyio_run:
                server.run(transport="stdio", log_level="CRITICAL")

                # Verify anyio.run was called
                mock_anyio_run.assert_called_once()

                # Get the function that was passed to anyio.run
                called_func = mock_anyio_run.call_args[0][0]

                # The function should be a partial that includes log_level
                assert hasattr(called_func, "keywords")
                assert called_func.keywords.get("log_level") == "CRITICAL"
