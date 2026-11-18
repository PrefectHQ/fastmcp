"""Change notifications on 2026-07-28 connections ride `subscriptions/listen`.

That era is sessionless, so a server has no standing channel to push a
`list_changed` notification on. A client opts in by opening a listen stream,
and `FastMCP.notify_*` publishes server-wide to every open stream, including
from background work with no request in flight.
"""

from __future__ import annotations

import asyncio

import anyio
import mcp_types as types
import pytest
from mcp.client.caching import CacheConfig, CacheKey, InMemoryResponseCacheStore
from mcp.client.subscriptions import ListenNotSupportedError
from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ResourceUpdated,
    ToolsListChanged,
)

from fastmcp import Client, Context, FastMCP
from fastmcp.tools import Tool
from fastmcp.utilities.tests import asgi_client


@pytest.fixture
def server() -> FastMCP:
    mcp = FastMCP("subscriptions")

    @mcp.tool
    def hello() -> str:
        return "hi"

    @mcp.resource("data://config")
    def config() -> str:
        return "v1"

    @mcp.prompt
    def greet() -> str:
        return "hello"

    return mcp


async def _next_event(subscription, timeout: float = 2):
    with anyio.fail_after(timeout):
        return await anext(aiter(subscription))


async def test_listen_is_served_and_capabilities_advertise_it(server: FastMCP):
    async with Client(server) as client:
        assert client.protocol_version == "2026-07-28"
        caps = client.server_capabilities
        assert caps is not None
        assert caps.tools is not None and caps.tools.list_changed is True
        assert caps.prompts is not None and caps.prompts.list_changed is True
        assert caps.resources is not None
        assert caps.resources.list_changed is True
        assert caps.resources.subscribe is True

        async with client.listen(tools_list_changed=True) as subscription:
            assert subscription.honored.tools_list_changed is True


async def test_out_of_band_tool_change_reaches_listener(server: FastMCP):
    """The #4920 repro: a change made with no request in flight."""
    async with Client(server) as client:
        assert {t.name for t in await client.list_tools()} == {"hello"}

        async with client.listen(tools_list_changed=True) as subscription:
            server.add_tool(Tool.from_function(lambda: "new", name="added"))
            await server.notify_tools_changed()

            assert isinstance(await _next_event(subscription), ToolsListChanged)
            assert {t.name for t in await client.list_tools()} == {"hello", "added"}


async def test_out_of_band_change_reaches_listener_over_http(server: FastMCP):
    async with asgi_client(server) as client:
        assert client.protocol_version == "2026-07-28"
        async with client.listen(tools_list_changed=True) as subscription:
            server.add_tool(Tool.from_function(lambda: "new", name="added"))
            await server.notify_tools_changed()

            assert isinstance(await _next_event(subscription), ToolsListChanged)
            assert {t.name for t in await client.list_tools()} == {"hello", "added"}


async def test_notify_from_inside_a_tool_reaches_listener(server: FastMCP):
    @server.tool
    async def reveal(ctx: Context) -> str:
        ctx.fastmcp.add_tool(
            Tool.from_function(lambda: "available", name="newly_available")
        )
        await ctx.fastmcp.notify_tools_changed()
        return "added"

    async with Client(server) as client:
        async with client.listen(tools_list_changed=True) as subscription:
            await client.call_tool("reveal")
            assert isinstance(await _next_event(subscription), ToolsListChanged)

        result = await client.call_tool("newly_available")
        assert isinstance(result.content[0], types.TextContent)
        assert result.content[0].text == "available"


@pytest.mark.parametrize(
    ("listen_kwargs", "publish", "expected"),
    [
        (
            {"prompts_list_changed": True},
            lambda s: s.notify_prompts_changed(),
            PromptsListChanged(),
        ),
        (
            {"resources_list_changed": True},
            lambda s: s.notify_resources_changed(),
            ResourcesListChanged(),
        ),
        (
            {"resource_subscriptions": ["data://config"]},
            lambda s: s.notify_resource_updated("data://config"),
            ResourceUpdated(uri="data://config"),
        ),
    ],
)
async def test_each_event_kind_reaches_listener(
    server: FastMCP, listen_kwargs, publish, expected
):
    async with Client(server) as client:
        async with client.listen(**listen_kwargs) as subscription:
            await publish(server)
            assert await _next_event(subscription) == expected


async def test_unrequested_kinds_are_filtered(server: FastMCP):
    async with Client(server) as client:
        async with client.listen(
            prompts_list_changed=True, resource_subscriptions=["data://config"]
        ) as subscription:
            await server.notify_tools_changed()
            await server.notify_resource_updated("data://other")
            await server.notify_prompts_changed()

            # The first event through is the one the stream asked for.
            assert await _next_event(subscription) == PromptsListChanged()


async def test_every_open_stream_receives_a_broadcast(server: FastMCP):
    async with Client(server) as first, Client(server) as second:
        async with (
            first.listen(tools_list_changed=True) as sub_a,
            second.listen(tools_list_changed=True) as sub_b,
        ):
            await server.notify_tools_changed()
            assert await _next_event(sub_a) == ToolsListChanged()
            assert await _next_event(sub_b) == ToolsListChanged()


async def test_notify_without_listeners_is_a_noop(server: FastMCP):
    await server.notify_tools_changed()
    await server.notify_prompts_changed()
    await server.notify_resources_changed()
    await server.notify_resource_updated("data://config")


class _SlowDeleteStore(InMemoryResponseCacheStore):
    """Widens the window between an event's arrival and its cache eviction."""

    async def delete(self, key: CacheKey) -> None:
        await asyncio.sleep(0.1)
        await super().delete(key)


async def test_listen_event_evicts_cached_list_before_yield():
    """A warm cache must not serve the stale list to an immediate refetch."""
    server = FastMCP("cached", cache_ttl=60)
    server.add_tool(Tool.from_function(lambda: "hi", name="hello"))
    cache = CacheConfig(store=_SlowDeleteStore(), partition="p", target_id="t")

    async with Client(server, cache=cache) as client:
        assert {t.name for t in await client.list_tools()} == {"hello"}

        async with client.listen(tools_list_changed=True) as subscription:
            server.add_tool(Tool.from_function(lambda: "new", name="added"))
            await server.notify_tools_changed()
            await _next_event(subscription)
            assert {t.name for t in await client.list_tools()} == {"hello", "added"}


async def test_listen_is_refused_on_legacy(server: FastMCP):
    async with Client(server, mode="legacy") as client:
        with pytest.raises(ListenNotSupportedError):
            async with client.listen(tools_list_changed=True):
                pass


async def test_legacy_context_notification_still_delivered(server: FastMCP):
    """The handshake era keeps its push channel; nothing about it changes."""
    received = asyncio.Event()

    async def handler(message):
        if isinstance(message, types.ToolListChangedNotification):
            received.set()

    @server.tool
    async def poke(ctx: Context) -> str:
        await ctx.send_notification(types.ToolListChangedNotification())
        return "ok"

    async with Client(server, mode="legacy", message_handler=handler) as client:
        caps = client.server_capabilities
        assert caps is not None
        assert caps.tools is not None and caps.tools.list_changed is True
        await client.call_tool("poke")
        with anyio.fail_after(2):
            await received.wait()


async def test_listen_events_also_reach_message_handler(server: FastMCP):
    received = asyncio.Event()

    async def handler(message):
        if isinstance(message, types.ToolListChangedNotification):
            received.set()

    async with Client(server, message_handler=handler) as client:
        async with client.listen(tools_list_changed=True) as subscription:
            await server.notify_tools_changed()
            await _next_event(subscription)
            with anyio.fail_after(2):
                await received.wait()
