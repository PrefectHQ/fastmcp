from mcp_types import ToolAnnotations

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.tool_injection import ToolInjectionMiddleware
from fastmcp.server.transforms import ReadOnly
from fastmcp.tools.base import Tool
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


async def test_injected_tools_follow_read_only_filter_and_keep_name_precedence():
    server = FastMCP("reader", transforms=[ReadOnly()])

    @server.tool(annotations={"readOnlyHint": True}, task=True)
    async def conflicting() -> str:
        return "provider tool"

    async def injected() -> str:
        return "injected tool"

    allowed = Tool.from_function(
        injected,
        name="allowed",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    denied = Tool.from_function(injected, name="conflicting", task=True)
    server.add_middleware(ToolInjectionMiddleware([allowed, denied]))

    assert not await server.get_tasks()
    async with Client(server) as client:
        assert [t.name for t in await client.list_tools()] == ["allowed"]
        assert (await client.call_tool("allowed")).data == "injected tool"
        assert (await client.call_tool("conflicting", raise_on_error=False)).is_error
