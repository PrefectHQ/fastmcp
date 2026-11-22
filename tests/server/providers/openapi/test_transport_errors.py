"""Transport error propagation from OpenAPI components."""

from collections.abc import Callable

import httpx2
import pytest
from mcp_types import TextContent

from fastmcp import Client, FastMCP, FastMCPDeprecationWarning
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Slow API", "version": "1.0"},
    "servers": [{"url": "https://api.example.test"}],
    "paths": {
        "/slow": {
            "get": {
                "operationId": "slow",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def _upstream(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="https://api.example.test",
        transport=httpx2.MockTransport(handler),
    )


async def _call_slow(server: FastMCP) -> str:
    async with Client(server) as client:
        result = await client.call_tool("slow", {}, raise_on_error=False)
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


async def test_masked_tool_timeout_returns_actionable_message() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("simulated", request=request)

    async with _upstream(handler) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=SPEC, client=http_client, mask_error_details=True
        )
        text = await _call_slow(server)

    assert text == "Upstream request timed out, please retry"


async def test_masked_resource_timeout_returns_actionable_message() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("simulated", request=request)

    async with _upstream(handler) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=SPEC,
            client=http_client,
            mask_error_details=True,
            route_maps=[RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE)],
        )
        async with Client(server) as client:
            [resource] = await client.list_resources()
        with pytest.raises(ResourceError) as exc_info:
            await server.read_resource(str(resource.uri))

    assert str(exc_info.value) == "Upstream request timed out, please retry"


async def test_masked_ordinary_error_stays_generic() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("boom")

    async with _upstream(handler) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=SPEC, client=http_client, mask_error_details=True
        )
        text = await _call_slow(server)

    assert text == "Error calling tool 'slow'"


async def test_unmasked_ordinary_error_keeps_details() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise RuntimeError("boom")

    async with _upstream(handler) as http_client:
        server = FastMCP.from_openapi(openapi_spec=SPEC, client=http_client)
        text = await _call_slow(server)

    assert text == "Error calling tool 'slow': boom"


async def test_legacy_request_error_remains_normalized() -> None:
    httpx = pytest.importorskip("httpx", reason="legacy httpx not installed")

    def handler(request: "httpx.Request") -> "httpx.Response":
        raise httpx.ConnectError("transport failed", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.example.test"
    ) as client:
        with pytest.warns(FastMCPDeprecationWarning):
            server = FastMCP.from_openapi(SPEC, client=client)
        with pytest.raises(ToolError) as exc_info:
            await server.call_tool("slow", {})

    assert str(exc_info.value) == (
        "Error calling tool 'slow': Request error (ConnectError): transport failed"
    )
