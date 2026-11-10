"""Resource discovery without the optional Prefab renderer."""

import sys
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.apps.config import PrefabAppConfig
from fastmcp.server import create_proxy
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.utilities.prefab import prefab_available


async def test_resource_listing_skips_tool_discovery_without_prefab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_list_calls = 0

    class CountToolLists(Middleware):
        async def on_list_tools(
            self, context: MiddlewareContext, call_next: CallNext
        ) -> Any:
            nonlocal tool_list_calls
            tool_list_calls += 1
            return await call_next(context)

    backend = FastMCP("backend", middleware=[CountToolLists()])

    @backend.tool
    def greet() -> str:
        return "hello"

    @backend.resource("data://status")
    def status() -> str:
        return "ready"

    with monkeypatch.context() as unavailable:
        unavailable.setitem(sys.modules, "prefab_ui", None)
        prefab_available.cache_clear()
        try:
            async with Client(create_proxy(backend)) as client:
                resources = await client.list_resources()
            assert [str(resource.uri) for resource in resources] == ["data://status"]
            assert tool_list_calls == 0
        finally:
            prefab_available.cache_clear()


async def test_preloaded_prefab_without_spec_remains_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefab = pytest.importorskip("prefab_ui")
    pytest.importorskip("prefab_ui.renderer")
    server = FastMCP("preloaded-prefab")

    @server.resource("data://status")
    def status() -> str:
        return "ready"

    @server.tool(app=PrefabAppConfig())
    def view() -> str:
        return "view"

    async with Client(server) as client:
        tool = next(tool for tool in await client.list_tools() if tool.name == "view")
        uri = tool.meta["ui"]["resourceUri"]
        with monkeypatch.context() as unavailable:
            unavailable.setattr(prefab, "__spec__", None)
            resources = {
                str(resource.uri) for resource in await client.list_resources()
            }
            assert "data://status" in resources
            assert uri in resources
            assert (await client.read_resource(uri))[0].text
