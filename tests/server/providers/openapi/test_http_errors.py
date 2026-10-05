"""Upstream HTTP error responses from OpenAPI components."""

import logging

import httpx2
import pytest
from mcp_types import TextContent

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Items API", "version": "1.0"},
    "servers": [{"url": "https://api.example.test"}],
    "paths": {
        "/items/{item_id}": {
            "get": {
                "operationId": "get_item",
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

RESOURCE_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Items API", "version": "1.0"},
    "servers": [{"url": "https://api.example.test"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def _upstream(status: int) -> httpx2.AsyncClient:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"detail": "nope"})

    return httpx2.AsyncClient(
        base_url="https://api.example.test",
        transport=httpx2.MockTransport(respond),
    )


async def _call_get_item(server: FastMCP) -> str:
    async with Client(server) as client:
        result = await client.call_tool(
            "get_item", {"item_id": "x1"}, raise_on_error=False
        )
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "Unauthorized"), (404, "Not Found"), (500, "Internal Server Error")],
)
async def test_http_error_is_logged_without_traceback(
    status: int, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    async with _upstream(status) as http_client:
        server = FastMCP.from_openapi(openapi_spec=SPEC, client=http_client)
        with caplog.at_level(logging.DEBUG, logger="fastmcp.server.server"):
            text = await _call_get_item(server)

    assert text == (
        f"Error calling tool 'get_item': HTTP error {status}: {reason} - "
        "{'detail': 'nope'}"
    )
    records = [
        r for r in caplog.records if r.getMessage() == "Error calling tool 'get_item'"
    ]
    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert not records[0].exc_info


async def test_http_429_returns_rate_limit_message() -> None:
    async with _upstream(429) as http_client:
        server = FastMCP.from_openapi(openapi_spec=SPEC, client=http_client)
        text = await _call_get_item(server)

    assert text == "Rate limited by upstream API, please retry later"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, "Error calling tool 'get_item'"),
        (429, "Rate limited by upstream API, please retry later"),
    ],
)
async def test_masked_http_error_hides_upstream_body(
    status: int, expected: str
) -> None:
    async with _upstream(status) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=SPEC, client=http_client, mask_error_details=True
        )
        text = await _call_get_item(server)

    assert text == expected


async def test_resource_http_429_returns_rate_limit_message() -> None:
    async with _upstream(429) as http_client:
        server = FastMCP.from_openapi(
            openapi_spec=RESOURCE_SPEC,
            client=http_client,
            route_maps=[RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE)],
        )
        async with Client(server) as client:
            [resource] = await client.list_resources()
        with pytest.raises(ResourceError) as exc_info:
            await server.read_resource(str(resource.uri))

    assert str(exc_info.value) == "Rate limited by upstream API, please retry later"
