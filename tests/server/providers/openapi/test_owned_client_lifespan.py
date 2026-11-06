"""Regression tests for the lifecycle of an OpenAPIProvider's owned client.

An ``OpenAPIProvider`` that creates its own ``httpx2.AsyncClient`` used to
enter the same client on every server lifespan. httpx2 clients cannot be
reopened after ``aclose()``, so a second connection failed with
``RuntimeError: Cannot reopen a client instance, once it has been closed.``
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.providers.openapi.routing import MCPType, RouteMap

SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "Tiny API", "version": "1.0.0"},
    "servers": [{"url": "http://127.0.0.1:9"}],
    "paths": {
        "/items": {
            "get": {
                "operationId": "list_items",
                "summary": "List items",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                }
                            }
                        },
                    }
                },
            }
        },
        "/health": {
            "get": {
                "operationId": "get_health",
                "summary": "Health",
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/items/{item_id}": {
            "get": {
                "operationId": "get_item",
                "summary": "Get item",
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
    },
}

TOOL_NAMES = {"list_items", "get_health", "get_item"}

ROUTE_MAPS = [
    RouteMap(pattern=r"^/health$", mcp_type=MCPType.RESOURCE),
    RouteMap(pattern=r"^/items/\{item_id\}$", mcp_type=MCPType.RESOURCE_TEMPLATE),
]


def _provider(mcp: FastMCP) -> OpenAPIProvider:
    provider = next(p for p in mcp.providers if isinstance(p, OpenAPIProvider))
    return provider


async def test_two_sequential_clients_on_default_client_server():
    """A default-client server can be connected to more than once."""
    mcp = FastMCP.from_openapi(SPEC, name="Tiny")

    for _ in range(2):
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == TOOL_NAMES


async def test_same_client_instance_can_be_reentered():
    """Re-entering the same in-memory Client works across server lifespans."""
    mcp = FastMCP.from_openapi(SPEC, name="Tiny")
    client = Client(mcp)

    async with client:
        assert {tool.name for tool in await client.list_tools()} == TOOL_NAMES

    async with client:
        assert {tool.name for tool in await client.list_tools()} == TOOL_NAMES


async def test_supplied_client_is_not_closed_or_replaced():
    """A caller-supplied client is neither closed nor swapped out."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"ok": True})

    supplied = httpx2.AsyncClient(
        base_url="http://upstream", transport=httpx2.MockTransport(handler)
    )
    mcp = FastMCP.from_openapi(SPEC, client=supplied, name="Tiny")
    provider = _provider(mcp)

    async with Client(mcp) as client:
        assert provider._client is supplied
        assert not supplied.is_closed
        assert {tool.name for tool in await client.list_tools()} == TOOL_NAMES

    assert not supplied.is_closed
    response = await supplied.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_first_lifespan_reuses_original_client_across_concurrent_entries():
    """Concurrent server lifespans keep the first-connect client open and shared."""
    mcp = FastMCP.from_openapi(SPEC, name="Tiny")
    provider = _provider(mcp)
    original = provider._client
    seen: list[httpx2.AsyncClient] = []
    both_entered = asyncio.Event()

    async def run_client() -> set[str]:
        async with Client(mcp) as client:
            seen.append(provider._client)
            assert not provider._client.is_closed
            if len(seen) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=5)
            return {tool.name for tool in await client.list_tools()}

    results = await asyncio.gather(run_client(), run_client())

    assert results == [TOOL_NAMES, TOOL_NAMES]
    assert seen == [original, original]
    assert original.is_closed


async def test_relifespan_recreates_and_rebinds_owned_client():
    """A closed owned client is replaced and every component is rebound."""
    mcp = FastMCP.from_openapi(SPEC, name="Tiny", route_maps=ROUTE_MAPS)
    provider = _provider(mcp)
    first_client = provider._client

    async with Client(mcp) as client:
        assert provider._client is first_client
        assert not first_client.is_closed
        assert len(await client.list_tools()) == 1

    assert first_client.is_closed

    async with Client(mcp) as client:
        second_client = provider._client
        assert second_client is not first_client
        assert not second_client.is_closed

        assert [tool.name for tool in await client.list_tools()] == ["list_items"]
        assert len(provider._tools) == 1
        assert len(provider._resources) == 1
        assert len(provider._templates) == 1
        assert all(t._client is second_client for t in provider._tools.values())
        assert all(r._client is second_client for r in provider._resources.values())
        assert all(t._client is second_client for t in provider._templates.values())

        assert len(await client.list_resources()) == 1
        assert len(await client.list_resource_templates()) == 1
