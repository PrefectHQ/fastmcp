import json
from typing import Any

import pytest
from dirty_equals import Contains

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
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
    return await FastMCP.as_proxy(Client(transport=FastMCPTransport(fastmcp_server)))


async def test_create_proxy(fastmcp_server):
    """Test that the proxy server properly forwards requests to the original server."""
    # Create a client
    client = Client(transport=FastMCPTransport(fastmcp_server))

    server = await FastMCPProxy.from_client(client)

    assert isinstance(server, FastMCPProxy)
    assert isinstance(server, FastMCP)
    assert server.name == "FastMCP"


class TestTools:
    async def test_list_tools(self, proxy_server):
        tools = await proxy_server.list_tools()
        assert [t.name for t in tools] == Contains("greet", "add", "error_tool")

    async def test_list_tools_same_as_original(self, fastmcp_server, proxy_server):
        assert await proxy_server.list_tools() == await fastmcp_server.list_tools()

    async def test_call_tool_result_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        result = await fastmcp_server.call_tool("greet", {"name": "Alice"})
        proxy_result = await proxy_server.call_tool("greet", {"name": "Alice"})

        assert result == proxy_result

    async def test_call_tool_calls_tool(self, proxy_server):
        proxy_result = await proxy_server.call_tool("add", {"a": 1, "b": 2})

        assert proxy_result[0].text == "3"

    async def test_error_tool_raises_error(self, proxy_server):
        with pytest.raises(ValueError, match="This is a test error"):
            await proxy_server.call_tool("error_tool", {})


class TestResources:
    async def test_list_resources(self, proxy_server):
        resources = await proxy_server.list_resources()
        assert [r.name for r in resources] == Contains(
            "data://users", "resource://wave"
        )

    async def test_list_resources_same_as_original(self, fastmcp_server, proxy_server):
        assert (
            await proxy_server.list_resources() == await fastmcp_server.list_resources()
        )

    async def test_read_resource(self, proxy_server: FastMCPProxy):
        result = await proxy_server.read_resource("resource://wave")
        assert result[0].content == "👋"  # type: ignore

    async def test_read_resource_same_as_original(self, fastmcp_server, proxy_server):
        result = await fastmcp_server.read_resource("resource://wave")
        proxy_result = await proxy_server.read_resource("resource://wave")
        assert proxy_result == result

    async def test_read_json_resource(self, proxy_server: FastMCPProxy):
        result = await proxy_server.read_resource("data://users")
        assert json.loads(result[0].content) == USERS  # type: ignore

    async def test_read_resource_returns_none_if_not_found(self, proxy_server):
        with pytest.raises(
            ValueError, match="Unknown resource: resource://nonexistent"
        ):
            await proxy_server.read_resource("resource://nonexistent")


class TestResourceTemplates:
    async def test_list_resource_templates(self, proxy_server):
        templates = await proxy_server.list_resource_templates()
        assert [t.name for t in templates] == Contains("get_user")

    async def test_list_resource_templates_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        result = await fastmcp_server.list_resource_templates()
        proxy_result = await proxy_server.list_resource_templates()
        assert proxy_result == result

    @pytest.mark.parametrize("id", [1, 2, 3])
    async def test_read_resource_template(self, proxy_server: FastMCPProxy, id: int):
        result = await proxy_server.read_resource(f"data://user/{id}")
        assert json.loads(result[0].content) == USERS[id - 1]  # type: ignore

    async def test_read_resource_template_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        result = await fastmcp_server.read_resource("data://user/1")
        proxy_result = await proxy_server.read_resource("data://user/1")
        assert proxy_result == result


class TestPrompts:
    async def test_list_prompts(self, proxy_server):
        prompts = await proxy_server.list_prompts()
        assert [p.name for p in prompts] == Contains("welcome")

    async def test_list_prompts_same_as_original(self, fastmcp_server, proxy_server):
        assert await proxy_server.list_prompts() == await fastmcp_server.list_prompts()

    async def test_render_prompt_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server
    ):
        result = await fastmcp_server.get_prompt("welcome", {"name": "Alice"})
        proxy_result = await proxy_server.get_prompt("welcome", {"name": "Alice"})
        assert proxy_result == result

    async def test_render_prompt_calls_prompt(self, proxy_server):
        result = await proxy_server.get_prompt("welcome", {"name": "Alice"})
        assert result.messages[0].content.text == "Welcome to FastMCP, Alice!"
