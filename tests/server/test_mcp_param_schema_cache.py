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
from packaging.version import Version
from starlette.testclient import TestClient

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransform

# Version-parse through packaging so pre-release mcp builds (e.g. "2.3rc1")
# do not blow up int().
MCP_SUPPORTS_PARAM_HOOK = Version(version("mcp")) >= Version("2.3")

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
        base,
        transform_args={
            "old_x": ArgTransform(name="x"),
            "old_y": ArgTransform(name="y"),
        },
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

    # Removal evicts the cache entry immediately, not only after the next
    # listing — a stale schema could otherwise keep header-validating a tool
    # that no longer exists.
    assert _hook(server)("temp") is None
    assert _hook(server)("keep") is not None

    async with Client(server) as client:
        await client.list_tools()

    # The removal evicted "temp" and the listing (which cannot see it anymore)
    # merges without resurrecting it, so it stays out.
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


async def test_auth_scoped_listing_does_not_evict_other_schemas():
    """An auth-filtered `tools/list` must not clear other principals' schemas.

    The cache is one shared server-wide dict: a listing visible to only one
    client (here: middleware filtering the catalog) merges into it instead of
    replacing it, so another client's already-cached schema keeps validating
    Mcp-Param headers instead of silently fail-opening (#5666 review).
    """

    server = FastMCP("t")

    @server.tool
    def alpha(x: int) -> int:
        return x

    @server.tool
    def beta(y: int) -> int:
        return y

    class OnlyBeta(Middleware):
        async def on_list_tools(self, context, call_next):
            return [tool for tool in await call_next(context) if tool.name == "beta"]

    server.add_middleware(OnlyBeta())

    async with Client(server) as client:
        listed = await client.list_tools()

    assert [tool.name for tool in listed] == ["beta"]
    # The scoped listing serves beta but must not evict alpha's schema.
    assert _hook(server)("beta") is not None
    assert _hook(server)("alpha") is not None
    assert "x" in _hook(server)("alpha")["properties"]


async def test_version_scoped_removal_keeps_surviving_version():
    """Evicting on remove_tool must not go further than the removed version."""

    server = FastMCP("t")
    server.add_tool(Tool.from_function(lambda: "v1", name="dup", version="1.0"))
    server.add_tool(Tool.from_function(lambda: "v2", name="dup", version="2.0"))

    schema = _hook(server)("dup")
    assert schema is not None

    # Removing one version re-seeds the cache from the surviving version
    # instead of evacuating the logical name.
    server._local_provider.remove_tool("dup", version="2.0")
    schema = _hook(server)("dup")
    assert schema is not None

    # Removing the last version evicts the entry entirely.
    server._local_provider.remove_tool("dup", version="1.0")
    assert _hook(server)("dup") is None
