"""Client-side response caching (SEP-2549) on ``fastmcp.Client`` (``cache=``).

At 2026-07-28 a server may mark a cacheable list result with a `ttlMs` freshness
hint; a client built with `cache=` serves subsequent identical calls from an
in-process store until the hint expires or a server notification evicts the entry.

The 2026 emitter is the SDK's `MCPServer` with `cache_hints=`, which stamps a
positive `ttlMs` onto `tools/list`. It is driven through a `fastmcp.Client` with
`mode="auto"` (negotiating 2026-07-28), so the cache coordinator runs entirely
through FastMCP's client.

Cache *honoring of server hints* is modern-only: a FastMCP or SDK server that
emits `ttlMs: 0` (no hint) is never cached, and legacy connections never read the
hint. That gating lives in the SDK's `ClientResponseCache`.
"""

from __future__ import annotations

import mcp_types
import pytest
from mcp.client.caching import CacheConfig
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer

from fastmcp import Client, FastMCP


def _cached_server(ttl_ms: int = 60000) -> MCPServer:
    """An SDK MCPServer whose tools/list carries a positive ttlMs hint at 2026."""
    server = MCPServer(
        "cached", cache_hints={"tools/list": CacheHint(ttl_ms=ttl_ms, scope="public")}
    )

    @server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    return server


class TestCacheConstruction:
    def test_cache_none_is_disabled_by_default(self):
        """Caching is opt-in: the default `cache=None` builds no cache, so a legacy
        connection is byte-identical to pre-v4 behavior (no handler wrapping)."""
        from fastmcp.client.tasks import TaskNotificationHandler

        client = Client(FastMCP("x"))
        assert client._response_cache is None
        # The message handler is the bare default, not a cache-evicting wrapper.
        assert isinstance(
            client._session_kwargs["message_handler"], TaskNotificationHandler
        )

    def test_cache_true_builds_default(self):
        client = Client(FastMCP("x"), cache=True)
        assert client._response_cache is not None

    def test_cache_false_disables(self):
        client = Client(FastMCP("x"), cache=False)
        assert client._response_cache is None

    def test_cache_config_custom(self):
        client = Client(FastMCP("x"), cache=CacheConfig(default_ttl_ms=1000))
        assert client._response_cache is not None

    def test_custom_store_requires_target_id(self):
        """A custom shared store cannot derive an identity from a FastMCP transport."""
        from mcp.client.caching import InMemoryResponseCacheStore

        with pytest.raises(ValueError, match="requires CacheConfig.target_id"):
            Client(
                FastMCP("x"),
                cache=CacheConfig(store=InMemoryResponseCacheStore(), partition="p"),
            )


class TestCacheServing:
    async def test_second_list_tools_served_from_cache(self):
        """A cached tools/list round-trip: the second call is served from the cache
        without a second wire request."""
        server = _cached_server()
        async with Client(server, mode="auto", cache=True) as client:
            # Confirm the server actually emits the freshness hint at 2026.
            raw = await client.session.list_tools()
            assert raw.ttl_ms == 60000

            first = await client.list_tools()

            calls = {"n": 0}
            original = client.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client.session.list_tools = spy  # type: ignore[method-assign]
            second = await client.list_tools()

        assert calls["n"] == 0  # served from cache, no wire round-trip
        assert [t.name for t in first] == [t.name for t in second] == ["add"]

    async def test_bypass_skips_cache(self):
        """`cache_mode="bypass"` always reaches the server."""
        server = _cached_server()
        async with Client(server, mode="auto", cache=True) as client:
            await client.list_tools_mcp()  # warm the cache

            calls = {"n": 0}
            original = client.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client.session.list_tools = spy  # type: ignore[method-assign]
            await client.list_tools_mcp(cache_mode="bypass")

        assert calls["n"] == 1


class TestCacheEviction:
    async def test_tool_list_changed_evicts(self):
        """A `notifications/tools/list_changed` routed through the composed message
        handler evicts the cached tools/list entry."""
        server = _cached_server()
        async with Client(server, mode="auto", cache=True) as client:
            await client.list_tools()
            cache = client._response_cache
            assert cache is not None
            assert await cache.read("tools/list", "") is not None

            # Drive the notification through the installed (cache-evicting) handler,
            # which composes eviction over FastMCP's own message handler chain.
            handler = client._session_kwargs["message_handler"]
            assert handler is not None
            await handler(
                mcp_types.ToolListChangedNotification(
                    method="notifications/tools/list_changed"
                )
            )

            assert await cache.read("tools/list", "") is None


class TestEraGating:
    async def test_legacy_does_not_honor_server_hint(self):
        """On a legacy connection the server hint is not read, so a hinted tools/list
        is not served from cache (the entry is never stored under the hint)."""
        server = _cached_server()
        async with Client(server, mode="legacy", cache=True) as client:
            await client.list_tools()
            cache = client._response_cache
            assert cache is not None
            # No modern hint honored -> nothing cached under the modern arm.
            assert await cache.read("tools/list", "") is None

    async def test_zero_ttl_server_not_cached_on_modern(self):
        """A modern server that emits `ttlMs: 0` (no hint) is not cached even though
        the connection is modern."""
        server = _cached_server(ttl_ms=0)
        async with Client(server, mode="auto", cache=True) as client:
            await client.list_tools()
            cache = client._response_cache
            assert cache is not None
            assert await cache.read("tools/list", "") is None


class TestNewIndependentCache:
    async def test_new_gets_independent_cache(self):
        """`new()` clones get their own cache, not the parent's entries."""
        parent = Client(_cached_server(), mode="auto", cache=True)
        child = parent.new()
        assert child._response_cache is not None
        assert child._response_cache is not parent._response_cache

    async def test_new_disabled_cache_stays_disabled(self):
        parent = Client(FastMCP("x"), cache=False)
        assert parent.new()._response_cache is None
