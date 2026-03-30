import pytest

from fastmcp import Client, Context, FastMCP


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def list_roots(context: Context) -> list[str]:
        roots = await context.list_roots()
        return [str(r.uri) for r in roots]

    return mcp


class TestClientRoots:
        @pytest.mark.asyncio
        async def test_set_roots_updates_connected_session(self, fastmcp_server: FastMCP):
            """
            set_roots should update the live session's roots callback if called at runtime.
            """
            initial_roots = ["file://a"]
            new_roots = ["file://b", "file://c"]
            async with Client(fastmcp_server, roots=initial_roots) as client:
                # Confirm initial roots
                result = await client.call_tool("list_roots", {})
                assert result.data == ["file://a"]
                # Update roots at runtime
                client.set_roots(new_roots)
                # Confirm new roots are reflected
                result2 = await client.call_tool("list_roots", {})
                assert result2.data == ["file://b", "file://c"]
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
        async with Client(fastmcp_server, roots=roots) as client:
            result = await client.call_tool("list_roots", {})
            assert result.data == [
                "file://x/y/z",
                "file://x/y/z",
            ]
