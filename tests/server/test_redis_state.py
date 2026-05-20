"""Tests for per-key TTL on set_state."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastmcp.server import Context, FastMCP


class TestPerKeyTTL:
    async def test_custom_ttl_stores_value(self):
        server = FastMCP("test")
        session = MagicMock()
        async with Context(server, session=session) as ctx:
            await ctx.set_state("short_lived", "data", ttl=60)
            assert await ctx.get_state("short_lived") == "data"

    async def test_default_ttl_stores_value(self):
        server = FastMCP("test")
        session = MagicMock()
        async with Context(server, session=session) as ctx:
            await ctx.set_state("default_ttl", "data")
            assert await ctx.get_state("default_ttl") == "data"
