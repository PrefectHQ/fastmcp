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
from fastmcp.resources import ResourceResult
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


class TestPerReadResourceCacheHints:
    """Per-read cache hints returned via ResourceResult."""

    async def test_resource_read_override_ttl_zero(self):
        """Resource returning ttl_ms=0 overrides server cache_ttl=3600."""
        mcp = FastMCP("x", cache_ttl=3600)

        @mcp.resource("data://stale")
        def stale_resource() -> ResourceResult:
            return ResourceResult("stale", ttl_ms=0)

        @mcp.resource("data://normal")
        def normal_resource() -> str:
            return "normal"

        async with Client(mcp, mode="auto") as client:
            listing = await client.session.list_resources()
            stale_read = await client.session.read_resource("data://stale")
            normal_read = await client.session.read_resource("data://normal")

        assert listing.ttl_ms == 3600000
        assert normal_read.ttl_ms == 3600000
        assert stale_read.ttl_ms == 0

    async def test_resource_template_per_read_hint_and_scope_override(self):
        """Resource template returning per-read hint overrides server scope."""
        mcp = FastMCP("x", cache_ttl=60, cache_scope="private")

        @mcp.resource("data://{key}")
        def get_data(key: str) -> ResourceResult:
            if key == "public_item":
                return ResourceResult("public", ttl_ms=5000, cache_scope="public")
            return ResourceResult("private", ttl_ms=1000)

        async with Client(mcp, mode="auto") as client:
            public_read = await client.session.read_resource("data://public_item")
            private_read = await client.session.read_resource("data://private_item")

        assert public_read.ttl_ms == 5000
        assert public_read.cache_scope == "public"
        assert private_read.ttl_ms == 1000
        assert private_read.cache_scope == "private"

    async def test_per_read_hint_without_server_cache_ttl(self):
        """Server has NO cache_ttl, but resource returns a per-read hint."""
        mcp = FastMCP("x")

        @mcp.resource("data://hinted")
        def hinted_resource() -> ResourceResult:
            return ResourceResult("hinted", ttl_ms=10000, cache_scope="public")

        async with Client(mcp, mode="auto") as client:
            read = await client.session.read_resource("data://hinted")

        assert read.ttl_ms == 10000
        assert read.cache_scope == "public"

    async def test_per_read_hint_wire_override_behavior(self):
        """Unset per-read hints receive server-wide fallback; explicit hints (including ttl_ms=0) reach the wire."""
        mcp = FastMCP("x", cache_ttl=60, cache_scope="private")

        @mcp.resource("data://default")
        def default_res() -> ResourceResult:
            return ResourceResult("default")

        @mcp.resource("data://custom")
        def custom_res() -> ResourceResult:
            return ResourceResult("custom", ttl_ms=0, cache_scope="public")

        async with Client(mcp, mode="auto") as client:
            default_read = await client.session.read_resource("data://default")
            custom_read = await client.session.read_resource("data://custom")

        # default_res used server hint (cache_ttl=60 -> 60000ms, cache_scope="private")
        assert default_read.ttl_ms == 60000
        assert default_read.cache_scope == "private"

        # custom_res explicitly overrode with ttl_ms=0 and cache_scope="public"
        assert custom_read.ttl_ms == 0
        assert custom_read.cache_scope == "public"

    async def test_per_read_ttl_ms_only_receives_default_private_scope(self):
        """Handler sets only ttl_ms=0 on a server with no cache_ttl. Scope falls back to the wire model's default ('private')."""
        mcp = FastMCP("x")

        @mcp.resource("data://stale-only-ttl")
        def stale_res() -> ResourceResult:
            return ResourceResult("stale", ttl_ms=0)

        async with Client(mcp, mode="auto") as client:
            read = await client.session.read_resource("data://stale-only-ttl")

        assert read.ttl_ms == 0
        assert read.cache_scope == "private"

    async def test_legacy_protocol_client_hinted_resource_succeeds(self):
        """A legacy-protocol client reading a hinted resource still succeeds."""
        mcp = FastMCP("x", cache_ttl=60)

        @mcp.resource("data://hinted")
        def hinted_resource() -> ResourceResult:
            return ResourceResult("hinted", ttl_ms=5000, cache_scope="public")

        async with Client(mcp, mode="legacy") as client:
            read = await client.read_resource("data://hinted")

        assert len(read) == 1
        assert read[0].text == "hinted"
