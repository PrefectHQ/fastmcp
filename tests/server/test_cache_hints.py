"""Server-level cache hints (SEP-2549) on the FastMCP constructor.

A FastMCP server opts every SDK-cacheable result it emits into client-side
caching with `cache_ttl` (seconds) and an optional `cache_scope`. The hint is
uniform by construction — one server-level value applies to `tools/list`,
`prompts/list`, `resources/list`, `resources/templates/list`, `resources/read`,
and `server/discover` alike. FastMCP passes the hint through to the SDK
low-level `Server(cache_hints=...)`, whose runner fills `ttlMs`/`cacheScope` on
every cacheable result; FastMCP never hand-sets the wire fields.

The end-to-end tests drive a FastMCP server through a `fastmcp.Client(cache=True)`
negotiating `2026-07-28`, proving the server-emitted hint and the client cache
interoperate — the server half of the feature whose client half is exercised in
`tests/client/client/test_response_cache.py`.
"""

from __future__ import annotations

import pytest
from mcp_types.methods import CACHEABLE_METHODS

from fastmcp import Client, FastMCP
from fastmcp.server.caching import build_cache_hints


class TestBuildCacheHints:
    def test_none_when_no_hint_set(self):
        assert build_cache_hints(None, None) is None

    def test_covers_every_cacheable_method(self):
        hints = build_cache_hints(60, "public")
        assert hints is not None
        assert set(hints) == set(CACHEABLE_METHODS)

    def test_seconds_converted_to_ms(self):
        hints = build_cache_hints(60, "public")
        assert hints is not None
        hint = hints["tools/list"]
        assert hint.ttl_ms == 60000
        assert hint.scope == "public"

    def test_scope_defaults_to_private(self):
        hints = build_cache_hints(30, None)
        assert hints is not None
        assert hints["tools/list"].scope == "private"

    @pytest.mark.parametrize("cache_ttl", [0, -1])
    def test_non_positive_ttl_rejected(self, cache_ttl):
        with pytest.raises(ValueError, match="cache_ttl must be a positive integer"):
            build_cache_hints(cache_ttl, None)

    @pytest.mark.parametrize("scope", ["public", "private"])
    def test_scope_without_ttl_rejected(self, scope):
        with pytest.raises(ValueError, match="cache_scope requires cache_ttl"):
            build_cache_hints(None, scope)


class TestConstructorValidation:
    @pytest.mark.parametrize("cache_ttl", [0, -5])
    def test_non_positive_ttl_raises(self, cache_ttl):
        with pytest.raises(ValueError, match="cache_ttl must be a positive integer"):
            FastMCP("x", cache_ttl=cache_ttl)

    def test_scope_without_ttl_raises(self):
        with pytest.raises(ValueError, match="cache_scope requires cache_ttl"):
            FastMCP("x", cache_scope="public")

    def test_no_cache_params_is_valid(self):
        # A server with no cache params constructs cleanly and emits no hint.
        FastMCP("x")


class TestServerEmitsHints:
    """A hinted server sets the wire fields the SDK client cache reads."""

    async def test_tools_list_carries_hint(self):
        mcp = FastMCP("x", cache_ttl=60, cache_scope="public")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="auto") as client:
            result = await client.session.list_tools()

        assert result.ttl_ms == 60000
        assert result.cache_scope == "public"

    async def test_prompts_list_carries_hint(self):
        mcp = FastMCP("x", cache_ttl=45, cache_scope="public")

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}"

        async with Client(mcp, mode="auto") as client:
            result = await client.session.list_prompts()

        assert result.ttl_ms == 45000
        assert result.cache_scope == "public"

    async def test_resources_list_and_read_carry_hint(self):
        mcp = FastMCP("x", cache_ttl=120, cache_scope="public")

        @mcp.resource("data://config")
        def config() -> str:
            return "value"

        async with Client(mcp, mode="auto") as client:
            listing = await client.session.list_resources()
            read = await client.session.read_resource("data://config")

        assert listing.ttl_ms == 120000
        assert listing.cache_scope == "public"
        assert read.ttl_ms == 120000
        assert read.cache_scope == "public"

    async def test_resource_templates_list_carries_hint(self):
        mcp = FastMCP("x", cache_ttl=90)

        @mcp.resource("data://{key}/value")
        def item(key: str) -> str:
            return key

        async with Client(mcp, mode="auto") as client:
            listing = await client.session.list_resource_templates()

        assert listing.ttl_ms == 90000

    async def test_scope_defaults_to_private(self):
        mcp = FastMCP("x", cache_ttl=30)

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="auto") as client:
            result = await client.session.list_tools()

        assert result.ttl_ms == 30000
        assert result.cache_scope == "private"


class TestEndToEndInterop:
    """A FastMCP server + `fastmcp.Client(cache=True)`: a hinted listing serves
    from the cache with no second wire request, an unhinted one does not."""

    async def test_hinted_list_tools_served_from_cache(self):
        mcp = FastMCP("x", cache_ttl=60, cache_scope="public")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="auto", cache=True) as client:
            await client.list_tools()

            calls = {"n": 0}
            original = client.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client.session.list_tools = spy  # type: ignore[method-assign]
            second = await client.list_tools()

        assert calls["n"] == 0  # served from cache, no second wire request
        assert [t.name for t in second] == ["add"]

    async def test_unhinted_server_not_cached(self):
        mcp = FastMCP("x")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="auto", cache=True) as client:
            await client.list_tools()

            calls = {"n": 0}
            original = client.session.list_tools

            async def spy(**kwargs):
                calls["n"] += 1
                return await original(**kwargs)

            client.session.list_tools = spy  # type: ignore[method-assign]
            await client.list_tools()

        assert calls["n"] == 1  # nothing cached, a second wire request happens

    async def test_private_scope_respected(self):
        mcp = FastMCP("x", cache_ttl=60, cache_scope="private")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="auto") as client:
            result = await client.session.list_tools()

        assert result.ttl_ms == 60000
        assert result.cache_scope == "private"

    async def test_hinted_read_resource_carries_cacheable_fields(self):
        """The server sets the wire fields the SDK client cache reads on a
        `resources/read` result, so a cache-capable client can reuse it."""
        mcp = FastMCP("x", cache_ttl=120, cache_scope="public")

        @mcp.resource("data://config")
        def config() -> str:
            return "value"

        async with Client(mcp, mode="auto", cache=True) as client:
            result = await client.session.read_resource("data://config")

        assert result.ttl_ms == 120000
        assert result.cache_scope == "public"
