"""Routable transport headers (SEP-2243) survive a FastMCP HTTP round trip.

The MCP Python SDK emits the routing headers on the client (`ClientSession`) and
validates them on the modern streamable-HTTP server transport. These tests are
FastMCP's regression guard: they prove FastMCP's HTTP layer neither strips nor
blocks the headers, so a gateway sitting in front of a FastMCP server can route
on them. The tool echoes back the raw request headers it received, letting the
test assert on exactly what reached the server.
"""

from typing import Annotated

from pydantic import Field

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.server import FastMCP
from fastmcp.utilities.tests import asgi_server


def _echo_server() -> FastMCP:
    server = FastMCP()

    @server.tool
    def echo_headers(
        tenant: Annotated[
            str, Field(json_schema_extra={"x-mcp-header": "Tenant"})
        ] = "acme",
    ) -> dict[str, str]:
        """Return the raw HTTP headers the server received for this request."""
        return dict(get_http_request().headers)

    return server


async def test_mcp_method_and_name_headers_reach_server():
    """`Mcp-Method` and `Mcp-Name` set by the SDK client arrive at the server."""
    async with asgi_server(_echo_server(), transport="http") as running_server:
        async with running_server.client() as client:
            result = await client.call_tool("echo_headers")

    headers = result.data
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "echo_headers"


async def test_mcp_param_header_reaches_server():
    """An `x-mcp-header` annotated parameter is mirrored into `Mcp-Param-*`.

    The SDK client only emits `Mcp-Param-*` once it has seen the tool's input
    schema, so the test lists tools before calling.
    """
    async with asgi_server(_echo_server(), transport="http") as running_server:
        async with running_server.client() as client:
            await client.list_tools()
            result = await client.call_tool("echo_headers", {"tenant": "beta-corp"})

    headers = result.data
    assert headers["mcp-param-tenant"] == "beta-corp"


async def test_routing_headers_survive_host_origin_protection():
    """The Host/Origin request guard does not strip the routing headers."""
    async with asgi_server(
        _echo_server(),
        transport="http",
        host_origin_protection=True,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    ) as running_server:
        async with running_server.client() as client:
            await client.list_tools()
            result = await client.call_tool("echo_headers", {"tenant": "gamma"})

    headers = result.data
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "echo_headers"
    assert headers["mcp-param-tenant"] == "gamma"


async def test_x_mcp_header_annotation_survives_schema_generation():
    """FastMCP preserves `x-mcp-header` in a tool's advertised input schema.

    This is the annotation the SDK client reads to decide which arguments to
    mirror into `Mcp-Param-*` headers, so it must reach the wire unchanged.
    """
    server = _echo_server()
    tools = await server._list_tools()
    (tool,) = [t for t in tools if t.name == "echo_headers"]
    assert tool.parameters["properties"]["tenant"]["x-mcp-header"] == "Tenant"
