from unittest.mock import AsyncMock, patch

import fastmcp
from fastmcp import FastMCP
from fastmcp.utilities.tests import temporary_settings


class TestTemporarySettings:
    def test_temporary_settings(self):
        assert fastmcp.settings.log_level == "DEBUG"
        with temporary_settings(log_level="ERROR"):
            assert fastmcp.settings.log_level == "ERROR"
        assert fastmcp.settings.log_level == "DEBUG"


class TestTransportSetting:
    def test_transport_default_is_stdio(self):
        assert fastmcp.settings.transport == "stdio"

    def test_transport_setting_can_be_changed(self):
        with temporary_settings(transport="http"):
            assert fastmcp.settings.transport == "http"
        assert fastmcp.settings.transport == "stdio"

    async def test_run_async_uses_transport_setting(self):
        mcp = FastMCP("test")
        with temporary_settings(transport="http"):
            with patch.object(
                mcp, "run_http_async", new_callable=AsyncMock
            ) as mock_http:
                await mcp.run_async()
                mock_http.assert_called_once()

    async def test_run_async_explicit_transport_overrides_setting(self):
        mcp = FastMCP("test")
        with temporary_settings(transport="http"):
            with patch.object(
                mcp, "run_stdio_async", new_callable=AsyncMock
            ) as mock_stdio:
                await mcp.run_async(transport="stdio")
                mock_stdio.assert_called_once()
