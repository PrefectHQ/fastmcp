"""Legacy-httpx client compatibility for the OpenAPI integration.

The upgrade guide promises that an existing legacy ``httpx.AsyncClient`` passed
to ``OpenAPIProvider``/``FastMCP.from_openapi`` keeps working via duck-typing.
That requires two things of the OpenAPI request path: requests must be built
through the user's own client (``build_request``), and errors raised by that
client — which are legacy-httpx exceptions, not httpx2 — must still receive the
integration's specific error formatting rather than surfacing as generic
failures.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.openapi import OpenAPIProvider

httpx = pytest.importorskip("httpx", reason="legacy httpx not installed")

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Legacy Client API", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "summary": "List items",
                "responses": {
                    "200": {
                        "description": "Items",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "items": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    },
}


def _legacy_client(handler) -> "httpx.AsyncClient":
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://api.example.com")


def _server(client) -> FastMCP:
    mcp = FastMCP("Legacy Client Server")
    mcp.add_provider(OpenAPIProvider(openapi_spec=SPEC, client=client))
    return mcp


async def test_tool_call_with_legacy_client_succeeds():
    """A legacy httpx.AsyncClient drives an OpenAPI tool end-to-end."""

    def handler(request: "httpx.Request") -> "httpx.Response":
        assert isinstance(request, httpx.Request)
        return httpx.Response(200, json={"items": ["a", "b"]})

    async with _legacy_client(handler) as client:
        async with Client(_server(client)) as mcp_client:
            result = await mcp_client.call_tool("list_items", {})
            assert result.structured_content == {"items": ["a", "b"]}


async def test_tool_http_error_keeps_openapi_formatting_with_legacy_client():
    """A legacy client's HTTP error still gets the integration's message format.

    The handler raises legacy ``httpx.HTTPStatusError``; the catch tuples must
    recognize it so the error carries the formatted status + body rather than a
    generic failure.
    """

    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(500, json={"detail": "boom"})

    async with _legacy_client(handler) as client:
        async with Client(_server(client)) as mcp_client:
            with pytest.raises(ToolError, match="HTTP error 500") as excinfo:
                await mcp_client.call_tool("list_items", {})
            assert "boom" in str(excinfo.value)


async def test_tool_request_error_keeps_openapi_formatting_with_legacy_client():
    """A legacy client's transport error maps to the formatted request error."""

    def handler(request: "httpx.Request") -> "httpx.Response":
        raise httpx.ConnectError("connection refused")

    async with _legacy_client(handler) as client:
        async with Client(_server(client)) as mcp_client:
            with pytest.raises(ToolError, match="Request error"):
                await mcp_client.call_tool("list_items", {})
