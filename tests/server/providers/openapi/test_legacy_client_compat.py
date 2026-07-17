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


async def test_multipart_tool_call_with_legacy_client():
    """Multipart bodies must materialize and send through a legacy client too."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/upload": {
                "post": {
                    "operationId": "upload_file",
                    "summary": "Upload a file",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Uploaded",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    received: dict[str, object] = {}

    def handler(request: "httpx.Request") -> "httpx.Response":
        received["content_type"] = request.headers.get("content-type", "")
        received["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    async with _legacy_client(handler) as client:
        mcp = FastMCP("Legacy Multipart Server")
        mcp.add_provider(OpenAPIProvider(openapi_spec=spec, client=client))
        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool("upload_file", {"file": "data"})
            assert result.structured_content == {"ok": True}

    content_type = received["content_type"]
    assert isinstance(content_type, str)
    assert "multipart/form-data" in content_type
    body = received["body"]
    assert isinstance(body, bytes)
    assert b"data" in body
