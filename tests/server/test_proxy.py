import json
from typing import Any

import mcp.types
import pytest
from dirty_equals import Contains
from mcp import McpError

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.proxy import FastMCPProxy

USERS = [
    {"id": "1", "name": "Alice", "active": True},
    {"id": "2", "name": "Bob", "active": True},
    {"id": "3", "name": "Charlie", "active": False},
]


@pytest.fixture
def fastmcp_server():
    server = FastMCP("TestServer")

    # --- Tools ---

    @server.tool()
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool()
    def error_tool():
        """This tool always raises an error."""
        raise ValueError("This is a test error")

    # --- Resources ---

    @server.resource(uri="resource://wave")
    def wave() -> str:
        return "👋"

    @server.resource(uri="data://users")
    async def get_users() -> list[dict[str, Any]]:
        return USERS

    @server.resource(uri="data://user/{user_id}")
    async def get_user(user_id: str) -> dict[str, Any] | None:
        return next((user for user in USERS if user["id"] == user_id), None)

    # --- Prompts ---

    @server.prompt()
    def welcome(name: str) -> str:
        return f"Welcome to FastMCP, {name}!"

    return server


@pytest.fixture
async def proxy_server(fastmcp_server):
    """Fixture that creates a FastMCP proxy server."""
    return FastMCP.as_proxy(Client(transport=FastMCPTransport(fastmcp_server)))


async def test_create_proxy(fastmcp_server):
    """Test that the proxy server properly forwards requests to the original server."""
    # Create a client
    client = Client(transport=FastMCPTransport(fastmcp_server))

    server = FastMCPProxy.as_proxy(client)

    assert isinstance(server, FastMCPProxy)
    assert isinstance(server, FastMCP)
    assert server.name == "FastMCP"


async def test_as_proxy_with_server(fastmcp_server):
    """FastMCP.as_proxy should accept a FastMCP instance."""
    proxy = FastMCP.as_proxy(fastmcp_server)
    result = await proxy._mcp_call_tool("greet", {"name": "Test"})
    assert isinstance(result[0], mcp.types.TextContent)
    assert result[0].text == "Hello, Test!"


async def test_as_proxy_with_transport(fastmcp_server):
    """FastMCP.as_proxy should accept a ClientTransport."""
    proxy = FastMCP.as_proxy(FastMCPTransport(fastmcp_server))
    result = await proxy._mcp_call_tool("greet", {"name": "Test"})
    assert isinstance(result[0], mcp.types.TextContent)
    assert result[0].text == "Hello, Test!"


def test_as_proxy_with_url():
    """FastMCP.as_proxy should accept a URL without connecting."""
    proxy = FastMCP.as_proxy("http://example.com/mcp")
    assert isinstance(proxy, FastMCPProxy)
    assert repr(proxy.client.transport).startswith("<StreamableHttp(")


class TestTools:
    async def test_get_tools(self, proxy_server):
        tools = await proxy_server.get_tools()
        assert "greet" in tools
        assert "add" in tools
        assert "error_tool" in tools

    async def test_list_tools_same_as_original(self, fastmcp_server, proxy_server):
        assert (
            await proxy_server._mcp_list_tools()
            == await fastmcp_server._mcp_list_tools()
        )

    async def test_call_tool_result_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        result = await fastmcp_server._mcp_call_tool("greet", {"name": "Alice"})
        proxy_result = await proxy_server._mcp_call_tool("greet", {"name": "Alice"})

        assert result == proxy_result

    async def test_call_tool_calls_tool(self, proxy_server):
        async with Client(proxy_server) as client:
            proxy_result = await client.call_tool("add", {"a": 1, "b": 2})

        assert isinstance(proxy_result[0], mcp.types.TextContent)
        assert proxy_result[0].text == "3"

    async def test_error_tool_raises_error(self, proxy_server):
        with pytest.raises(ToolError, match=""):
            async with Client(proxy_server) as client:
                await client.call_tool("error_tool", {})


class TestResources:
    async def test_get_resources(self, proxy_server):
        resources = await proxy_server.get_resources()
        assert [r.name for r in resources.values()] == Contains(
            "data://users", "resource://wave"
        )

    async def test_list_resources_same_as_original(self, fastmcp_server, proxy_server):
        assert (
            await proxy_server._mcp_list_resources()
            == await fastmcp_server._mcp_list_resources()
        )

    async def test_read_resource(self, proxy_server: FastMCPProxy):
        async with Client(proxy_server) as client:
            result = await client.read_resource("resource://wave")
        assert isinstance(result[0], mcp.types.TextResourceContents)
        assert result[0].text == "👋"

    async def test_read_resource_same_as_original(self, fastmcp_server, proxy_server):
        async with Client(fastmcp_server) as client:
            result = await client.read_resource("resource://wave")
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("resource://wave")
        assert proxy_result == result

    async def test_read_json_resource(self, proxy_server: FastMCPProxy):
        async with Client(proxy_server) as client:
            result = await client.read_resource("data://users")
        assert isinstance(result[0], mcp.types.TextResourceContents)
        assert json.loads(result[0].text) == USERS

    async def test_read_resource_returns_none_if_not_found(self, proxy_server):
        with pytest.raises(McpError, match="Unknown resource: resource://nonexistent"):
            async with Client(proxy_server) as client:
                await client.read_resource("resource://nonexistent")


class TestResourceTemplates:
    async def test_get_resource_templates(self, proxy_server):
        templates = await proxy_server.get_resource_templates()
        assert [t.name for t in templates.values()] == Contains("get_user")

    async def test_list_resource_templates_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        result = await fastmcp_server._mcp_list_resource_templates()
        proxy_result = await proxy_server._mcp_list_resource_templates()
        assert proxy_result == result

    @pytest.mark.parametrize("id", [1, 2, 3])
    async def test_read_resource_template(self, proxy_server: FastMCPProxy, id: int):
        async with Client(proxy_server) as client:
            result = await client.read_resource(f"data://user/{id}")
        assert isinstance(result[0], mcp.types.TextResourceContents)
        assert json.loads(result[0].text) == USERS[id - 1]

    async def test_read_resource_template_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        async with Client(fastmcp_server) as client:
            result = await client.read_resource("data://user/1")
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("data://user/1")
        assert proxy_result == result


class TestPrompts:
    async def test_get_prompts_server_method(self, proxy_server: FastMCPProxy):
        prompts = await proxy_server.get_prompts()
        assert [p.name for p in prompts.values()] == Contains("welcome")

    async def test_list_prompts_same_as_original(self, fastmcp_server, proxy_server):
        async with Client(fastmcp_server) as client:
            result = await client.list_prompts()
        async with Client(proxy_server) as client:
            proxy_result = await client.list_prompts()
        assert proxy_result == result

    async def test_render_prompt_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        async with Client(fastmcp_server) as client:
            result = await client.get_prompt("welcome", {"name": "Alice"})
        async with Client(proxy_server) as client:
            proxy_result = await client.get_prompt("welcome", {"name": "Alice"})
        assert proxy_result == result

    async def test_render_prompt_calls_prompt(self, proxy_server):
        async with Client(proxy_server) as client:
            result = await client.get_prompt("welcome", {"name": "Alice"})
        assert isinstance(result.messages[0], mcp.types.PromptMessage)
        assert result.messages[0].role == "user"
        assert isinstance(result.messages[0].content, mcp.types.TextContent)
        assert result.messages[0].content.text == "Welcome to FastMCP, Alice!"
