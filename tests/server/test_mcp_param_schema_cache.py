"""Wire-schema cache behind the SDK's `get_tool_input_schema` hook (mcp>=2.3).

Every argument-bearing `tools/call` made the SDK fall back to running the
server's full `tools/list` handler — all `on_list_tools` middleware, provider
enumeration, and request logging — just to find one tool's input schema
(#5652). FastMCP now serves the sync hook from a wire-schema cache: local tool
registration seeds it, and every `tools/list` response refreshes it (which also
covers proxied and mounted tools).
"""

from importlib.metadata import version

import pytest
from starlette.testclient import TestClient

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransform

MCP_SUPPORTS_PARAM_HOOK = (
    tuple(int(part) for part in version("mcp").split(".")[:2]) >= (2, 3)
)

pytestmark = pytest.mark.skipif(
    not MCP_SUPPORTS_PARAM_HOOK,
    reason="the get_tool_input_schema hook requires mcp>=2.3",
)


def _hook(server: FastMCP):
    return server._mcp_server.get_tool_input_schema


def test_hook_installed_when_sdk_supports_it():
    server = FastMCP("t")

    assert callable(_hook(server))
    # Unknown tools must not raise from the hook.
    assert _hook(server)("no-such-tool") is None


async def test_local_tool_available_before_any_listing():
    server = FastMCP("t")

    @server.tool
    def echo(text: str) -> str:
        return text

    schema = _hook(server)("echo")

    assert schema is not None
    assert "text" in schema["properties"]


async def test_transformed_schema_is_served():
    server = FastMCP("t")

    def add(old_x: int, old_y: int) -> int:
        return old_x + old_y

    base = Tool.from_function(add, name="add")
    renamed = Tool.from_tool(
        base, transform_args={"old_x": ArgTransform(name="x"), "old_y": ArgTransform(name="y")}
    )
    server.add_tool(renamed)

    schema = _hook(server)("add")

    assert schema is not None
    # The renamed (wire) parameters, not the original ones.
    assert set(schema["properties"]) == {"x", "y"}


async def test_listing_covers_mounted_tools():
    nested = FastMCP("nested")

    @nested.tool
    def remote(x: int) -> int:
        return x

    server = FastMCP("main")
    server.mount(nested, namespace="nested")

    # Mounted tools resolve asynchronously, so they can only enter the cache
    # through a listing.
    assert _hook(server)("nested_remote") is None

    async with Client(server) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["nested_remote"]
    schema = _hook(server)("nested_remote")
    assert schema is not None
    assert "x" in schema["properties"]


async def test_listing_refresh_replaces_stale_entries():
    server = FastMCP("t")

    @server.tool
    def keep(text: str) -> str:
        return text

    temp = Tool.from_function(lambda: "temp", name="temp")
    server.add_tool(temp)
    assert _hook(server)("temp") is not None

    server._local_provider.remove_tool("temp")

    async with Client(server) as client:
        await client.list_tools()

    # The listing refresh replaces the whole map, so removed tools drop out.
    assert _hook(server)("temp") is None
    assert _hook(server)("keep") is not None


def test_tools_call_does_not_run_list_middleware():
    listings = 0

    class CountListings(Middleware):
        async def on_list_tools(self, context, call_next):
            nonlocal listings
            listings += 1
            return await call_next(context)

    server = FastMCP("demo")
    server.add_middleware(CountListings())

    @server.tool
    def echo(text: str) -> str:
        return text

    app = server.http_app(stateless_http=True)
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "echo",
                    "arguments": {"text": "hi"},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "echo",
            },
        )
        assert response.status_code == 200, response.text[:300]
        assert "hi" in response.text

    # The argument-bearing call must not have run the tools/list handler.
    assert listings == 0
