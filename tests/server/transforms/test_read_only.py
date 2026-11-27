from fastmcp import FastMCP
from fastmcp.server.transforms import ReadOnly
from fastmcp.utilities.tests import asgi_client


async def test_read_only_view_shares_tools_with_full_endpoint():
    full = FastMCP("Full")
    calls = []

    @full.tool(annotations={"readOnlyHint": True})
    def count() -> int:
        return len(calls)

    @full.tool(annotations={"readOnlyHint": False})
    def append() -> None:
        calls.append(1)

    @full.tool
    def unannotated() -> None:
        calls.append(2)

    view = FastMCP("Read only")
    view.mount(full)
    view.add_transform(ReadOnly())

    async with (
        asgi_client(full, path="/mcp") as writer,
        asgi_client(view, path="/read-only/mcp") as reader,
    ):
        assert {tool.name for tool in await writer.list_tools()} == {
            "count",
            "append",
            "unannotated",
        }
        assert [tool.name for tool in await reader.list_tools()] == ["count"]
        await writer.call_tool("append")
        for name in ("append", "unannotated"):
            rejected = await reader.call_tool(name, raise_on_error=False)
            assert rejected.is_error
        assert (await reader.call_tool("count")).data == 1
