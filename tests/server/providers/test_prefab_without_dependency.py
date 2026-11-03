"""Resource discovery without the optional Prefab renderer."""

import sys
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.utilities.prefab import prefab_available


async def test_resource_listing_without_prefab_skips_backend_tools(
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
