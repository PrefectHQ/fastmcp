"""Cookie handling when an HTTP-served OpenAPI server calls its upstream API.

The incoming MCP request's `Cookie` header belongs to the MCP host and is not
copied onto requests sent to the OpenAPI upstream. Cookies that the server
configures on its own HTTP client, and cookies declared as operation
parameters, still reach the upstream.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.utilities.tests import run_server_async

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Cookie Test API", "version": "1.0.0"},
    "servers": [{"url": "https://upstream.example.com"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "create_item",
                "responses": {"200": {"description": "OK"}},
            },
        },
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
        },
        "/session": {
            "post": {
                "operationId": "check_session",
                "parameters": [
                    {
                        "name": "upstream_session",
                        "in": "cookie",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
}

ROUTE_MAPS = [
    RouteMap(
        methods=["GET"],
        pattern=r".*\{.*\}.*",
        mcp_type=MCPType.RESOURCE_TEMPLATE,
    ),
    RouteMap(methods=["GET"], mcp_type=MCPType.RESOURCE),
]

CallComponent = Callable[[Client], Awaitable[object]]


async def call_tool(client: Client) -> object:
    return await client.call_tool("create_item")


async def read_resource(client: Client) -> object:
    return await client.read_resource("resource://list_items")


async def read_resource_template(client: Client) -> object:
    return await client.read_resource("resource://get_item/item-1")


component_calls = pytest.mark.parametrize(
    "call_component",
    [call_tool, read_resource, read_resource_template],
    ids=["tool", "resource", "resource_template"],
)


@asynccontextmanager
async def mcp_client_for_openapi(
    seen_requests: list[httpx.Request],
    **httpx_client_kwargs: Any,
) -> AsyncIterator[Client]:
    """Serve an OpenAPI server over HTTP and connect a client that sends a cookie."""

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        base_url="https://upstream.example.com",
        transport=httpx.MockTransport(handler),
        **httpx_client_kwargs,
    ) as upstream_client:
        server = FastMCP.from_openapi(
            openapi_spec=OPENAPI_SPEC,
            client=upstream_client,
            route_maps=ROUTE_MAPS,
        )
        async with run_server_async(server, transport="http") as url:
            transport = StreamableHttpTransport(
                url,
                headers={
                    "Cookie": "mcp_session=mcp-host-marker",
                    "X-Forwarded-Marker": "kept",
                },
            )
            async with Client(transport=transport) as client:
                yield client


@component_calls
async def test_mcp_request_cookie_is_not_forwarded(call_component: CallComponent):
    seen_requests: list[httpx.Request] = []
    async with mcp_client_for_openapi(seen_requests) as client:
        await call_component(client)

    assert len(seen_requests) == 1
    assert "cookie" not in seen_requests[0].headers
    assert seen_requests[0].headers["x-forwarded-marker"] == "kept"


@component_calls
async def test_client_configured_cookie_header_reaches_upstream(
    call_component: CallComponent,
):
    seen_requests: list[httpx.Request] = []
    async with mcp_client_for_openapi(
        seen_requests,
        headers={"Cookie": "upstream_session=configured"},
    ) as client:
        await call_component(client)

    assert len(seen_requests) == 1
    assert seen_requests[0].headers["cookie"] == "upstream_session=configured"


async def test_client_cookie_jar_reaches_upstream_resource():
    seen_requests: list[httpx.Request] = []
    async with mcp_client_for_openapi(
        seen_requests,
        cookies={"upstream_session": "jar-value"},
    ) as client:
        await client.read_resource("resource://list_items")

    assert len(seen_requests) == 1
    assert seen_requests[0].headers["cookie"] == "upstream_session=jar-value"


async def test_declared_cookie_parameter_reaches_upstream():
    seen_requests: list[httpx.Request] = []
    async with mcp_client_for_openapi(seen_requests) as client:
        await client.call_tool("check_session", {"upstream_session": "declared"})

    assert len(seen_requests) == 1
    assert seen_requests[0].headers["cookie"] == "upstream_session=declared"
