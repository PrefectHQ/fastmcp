"""Deprecation bridge for legacy-httpx OpenAPI clients."""

import pytest

from fastmcp import Client, FastMCP, FastMCPDeprecationWarning
from fastmcp.exceptions import ToolError

httpx = pytest.importorskip("httpx", reason="legacy httpx not installed")

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Legacy Client API", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
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
        }
    },
}


async def test_legacy_client_warns_and_remains_usable() -> None:
    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(200, json={"items": ["a", "b"]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.example.com",
    ) as client:
        with pytest.warns(
            FastMCPDeprecationWarning,
            match="httpx.AsyncClient.*deprecated",
        ):
            server = FastMCP.from_openapi(SPEC, client=client)

        async with Client(server) as mcp_client:
            result = await mcp_client.call_tool("list_items", {})

    assert result.structured_content == {"items": ["a", "b"]}


async def test_legacy_client_preserves_http_error_details() -> None:
    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(404, json={"detail": "items not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.example.com",
    ) as client:
        with pytest.warns(FastMCPDeprecationWarning):
            server = FastMCP.from_openapi(SPEC, client=client)

        async with Client(server) as mcp_client:
            with pytest.raises(ToolError, match="HTTP error 404") as exc_info:
                await mcp_client.call_tool("list_items", {})

    assert "items not found" in str(exc_info.value)


@pytest.mark.parametrize(
    ("error_kind", "message"),
    [
        ("timeout", "HTTP request timed out (ReadTimeout)"),
        ("connect", "Request error (ConnectError)"),
    ],
)
async def test_legacy_client_preserves_transport_error_details(
    error_kind: str,
    message: str,
) -> None:
    def handler(request: "httpx.Request") -> "httpx.Response":
        if error_kind == "timeout":
            raise httpx.ReadTimeout("transport failed", request=request)
        raise httpx.ConnectError("transport failed", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.example.com",
    ) as client:
        with pytest.warns(FastMCPDeprecationWarning):
            server = FastMCP.from_openapi(SPEC, client=client)

        async with Client(server) as mcp_client:
            with pytest.raises(ToolError) as exc_info:
                await mcp_client.call_tool("list_items", {})

    assert message in str(exc_info.value)
