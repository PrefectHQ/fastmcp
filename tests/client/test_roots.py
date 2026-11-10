import functools

import pytest
from mcp_types import Root

from fastmcp import Client, Context, FastMCP


@pytest.fixture
def fastmcp_server():
    """A server that issues a handshake-era `roots/list` request.

    `Context` has no `list_roots()` — server-initiated requests are not part of
    FastMCP's server API. This server reaches the SDK session directly to stand
    in for a legacy upstream, so the client's `roots=` handling stays covered.
    """
    mcp = FastMCP()

    @mcp.tool
    async def list_roots(context: Context) -> list[str]:
        result = await context.session.list_roots()  # ty: ignore[deprecated]
        return [str(r.uri) for r in result.roots]

    return mcp


class TestClientRoots:
    @pytest.mark.parametrize("roots", [["x"], ["x", "y"]])
    async def test_invalid_roots(self, fastmcp_server: FastMCP, roots: list[str]):
        """
        Roots must be URIs
        """
        with pytest.raises(ValueError, match="Input should be a valid URL"):
            async with Client(fastmcp_server, roots=roots):
                pass

    @pytest.mark.parametrize("roots", [["https://x.com"]])
    async def test_invalid_urls(self, fastmcp_server: FastMCP, roots: list[str]):
        """
        At this time, root URIs must start with file://
        """
        with pytest.raises(ValueError, match="URL scheme should be 'file'"):
            async with Client(fastmcp_server, roots=roots):
                pass

    @pytest.mark.parametrize("roots", [["file://x/y/z", "file://x/y/z"]])
    async def test_valid_roots(self, fastmcp_server: FastMCP, roots: list[str]):
        # `roots/list` is a server-initiated request, so it only exists on the
        # handshake era; SEP-2577 removed it from the modern protocol.
        async with Client(fastmcp_server, mode="legacy", roots=roots) as client:
            result = await client.call_tool("list_roots", {})
            assert result.data == [
                "file://x/y/z",
                "file://x/y/z",
            ]

    async def test_roots_handler_answers_a_legacy_server(self, fastmcp_server: FastMCP):
        """A callable `roots=` handler still answers a legacy server's request."""
        calls: list[object] = []

        async def roots_handler(ctx) -> list[Root]:
            calls.append(ctx)
            return [Root(uri="file://from/handler")]

        async with Client(fastmcp_server, mode="legacy", roots=roots_handler) as client:
            result = await client.call_tool("list_roots", {})

        assert len(calls) == 1
        assert result.data == ["file://from/handler"]

    async def test_bound_method_roots_handler(self, fastmcp_server: FastMCP):
        class RootsProvider:
            async def get_roots(self, _context: object) -> list[str]:
                return ["file:///bound-method"]

        provider = RootsProvider()

        async with Client(
            fastmcp_server, mode="legacy", roots=provider.get_roots
        ) as client:
            result = await client.call_tool("list_roots", {})

        assert result.data == ["file:///bound-method"]

    async def test_partial_roots_handler(self, fastmcp_server: FastMCP):
        async def get_roots(prefix: str, _context: object) -> list[str]:
            return [f"file:///{prefix}"]

        handler = functools.partial(get_roots, "partial")

        async with Client(fastmcp_server, mode="legacy", roots=handler) as client:
            result = await client.call_tool("list_roots", {})

        assert result.data == ["file:///partial"]

    async def test_callable_object_roots_handler(self, fastmcp_server: FastMCP):
        class RootsProvider:
            async def __call__(self, _context: object) -> list[str]:
                return ["file:///callable-object"]

        async with Client(
            fastmcp_server, mode="legacy", roots=RootsProvider()
        ) as client:
            result = await client.call_tool("list_roots", {})

        assert result.data == ["file:///callable-object"]
