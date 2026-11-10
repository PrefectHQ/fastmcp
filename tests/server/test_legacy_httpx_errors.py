"""Compatibility tests for legacy-httpx exceptions raised by user code."""

import pytest

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError

httpx = pytest.importorskip("httpx", reason="legacy httpx not installed")


async def test_legacy_httpx_rate_limit_remains_actionable() -> None:
    server = FastMCP("Legacy httpx errors", mask_error_details=True)

    @server.tool
    def rate_limited() -> None:
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    with pytest.raises(ToolError, match="Rate limited by upstream API"):
        await server.call_tool("rate_limited", {})


async def test_legacy_httpx_resource_timeout_remains_actionable() -> None:
    server = FastMCP("Legacy httpx errors", mask_error_details=True)

    @server.resource("resource://timed-out")
    def timed_out() -> str:
        request = httpx.Request("GET", "https://example.com")
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ResourceError, match="Upstream request timed out"):
        await server.read_resource("resource://timed-out")
