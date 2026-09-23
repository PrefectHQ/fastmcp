"""Regression tests for ``cache_ttl`` honoring on ``ProxyProvider`` listings.

https://github.com/PrefectHQ/fastmcp/issues/5228 — ``ProxyProvider.cache_ttl``
did not apply to ``list_tools()``: every listing fetched the backend anew
even while the cache was still fresh, defeating the purpose of the cache
(the MCP SDK issues a ``tools/list`` before every ``tools/call``).
"""

from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider


class _CountingListMiddleware(Middleware):
    """Counts how many times the backend actually serves a tools listing."""

    def __init__(self):
        self.list_tools_calls = 0

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        self.list_tools_calls += 1
        return await call_next(context)


def _make_backend():
    server = FastMCP("Backend")

    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    counter = _CountingListMiddleware()
    server.add_middleware(counter)
    return server, counter


async def test_list_tools_served_from_fresh_cache():
    server, counter = _make_backend()
    provider = ProxyProvider(
        lambda: ProxyClient(transport=FastMCPTransport(server)),
        cache_ttl=60,
    )

    first = await provider.list_tools()
    second = await provider.list_tools()

    assert [t.name for t in first] == ["greet"]
    assert [t.name for t in second] == ["greet"]
    assert counter.list_tools_calls == 1


async def test_list_tools_refetched_after_ttl_expiry():
    server, counter = _make_backend()
    provider = ProxyProvider(
        lambda: ProxyClient(transport=FastMCPTransport(server)),
        cache_ttl=60,
    )

    await provider.list_tools()
    assert provider._tools_cache is not None
    # Age the cache past its TTL, then list again — the backend must be hit.
    provider._tools_cache.timestamp -= 61

    await provider.list_tools()

    assert counter.list_tools_calls == 2


async def test_list_tools_with_cache_ttl_zero_always_fetches():
    server, counter = _make_backend()
    provider = ProxyProvider(
        lambda: ProxyClient(transport=FastMCPTransport(server)),
        cache_ttl=0,
    )

    await provider.list_tools()
    await provider.list_tools()

    assert counter.list_tools_calls == 2
