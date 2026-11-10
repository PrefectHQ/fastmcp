"""Default component names survive operations without ASCII letters or digits."""

from typing import Any

import httpx2
import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap


def _spec(operations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Names", "version": "1"},
        "paths": {
            path: {"get": {**operation, "responses": {"200": {"description": "OK"}}}}
            for path, operation in operations.items()
        },
    }


def _client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="http://test",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})),
    )


@pytest.mark.parametrize(
    "operation",
    [{"summary": "获取用户"}, {"operationId": "!!!"}, {"operationId": "عرض"}],
)
async def test_tool_name_falls_back_to_method_and_path(operation: dict[str, Any]):
    server = FastMCP.from_openapi(
        openapi_spec=_spec({"/users": operation}), client=_client()
    )
    async with Client(server) as client:
        assert [tool.name for tool in await client.list_tools()] == ["GET_users"]


async def test_tools_without_usable_names_stay_distinct():
    spec = _spec(
        {
            "/users": {"summary": "获取用户"},
            "/orders": {"summary": "获取订单"},
            "/other": {"summary": "获取其他"},
        }
    )
    server = FastMCP.from_openapi(openapi_spec=spec, client=_client())
    async with Client(server) as client:
        names = sorted(tool.name for tool in await client.list_tools())
    assert names == ["GET_orders", "GET_other", "GET_users"]


async def test_resource_uri_without_usable_name():
    server = FastMCP.from_openapi(
        openapi_spec=_spec({"/users": {"summary": "获取用户"}}),
        client=_client(),
        route_maps=[RouteMap(mcp_type=MCPType.RESOURCE)],
    )
    async with Client(server) as client:
        resources = await client.list_resources()
    assert [str(resource.uri) for resource in resources] == ["resource://GET_users"]
