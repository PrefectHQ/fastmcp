import json
from typing import Any

import pytest
from anyio import create_task_group
from dirty_equals import Contains
from mcp import McpError
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
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

    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    @server.tool
    def tool_without_description() -> str:
        return "Hello?"

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool
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

    @server.prompt
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
    assert result[0].text == "Hello, Test!"  # type: ignore[attr-defined]


async def test_as_proxy_with_transport(fastmcp_server):
    """FastMCP.as_proxy should accept a ClientTransport."""
    proxy = FastMCP.as_proxy(FastMCPTransport(fastmcp_server))
    result = await proxy._mcp_call_tool("greet", {"name": "Test"})
    assert result[0].text == "Hello, Test!"  # type: ignore[attr-defined]


def test_as_proxy_with_url():
    """FastMCP.as_proxy should accept a URL without connecting."""
    proxy = FastMCP.as_proxy("http://example.com/mcp/")
    assert isinstance(proxy, FastMCPProxy)
    assert isinstance(proxy.client.transport, StreamableHttpTransport)
    assert proxy.client.transport.url == "http://example.com/mcp/"


class TestTools:
    async def test_get_tools(self, proxy_server):
        tools = await proxy_server.get_tools()
        assert "greet" in tools
        assert "add" in tools
        assert "error_tool" in tools
        assert "tool_without_description" in tools

    async def test_tool_without_description(self, proxy_server):
        tools = await proxy_server.get_tools()
        assert tools["tool_without_description"].description is None

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
        assert proxy_result[0].text == "3"  # type: ignore[attr-defined]

    async def test_error_tool_raises_error(self, proxy_server):
        with pytest.raises(ToolError, match="This is a test error"):
            async with Client(proxy_server) as client:
                await client.call_tool("error_tool", {})

    async def test_proxy_can_overwrite_proxied_tool(self, proxy_server):
        """
        Test that a tool defined on the proxy can overwrite the proxied tool with the same name.
        """

        @proxy_server.tool
        def greet(name: str, extra: str = "extra") -> str:
            return f"Overwritten, {name}! {extra}"

        async with Client(proxy_server) as client:
            result = await client.call_tool("greet", {"name": "Marvin", "extra": "abc"})
        assert result[0].text == "Overwritten, Marvin! abc"  # type: ignore[attr-defined]

    async def test_proxy_errors_if_overwritten_tool_is_disabled(self, proxy_server):
        """
        Test that a tool defined on the proxy is not listed if it is disabled,
        and it doesn't fall back to the proxied tool with the same name
        """

        @proxy_server.tool(enabled=False)
        def greet(name: str, extra: str = "extra") -> str:
            return f"Overwritten, {name}! {extra}"

        async with Client(proxy_server) as client:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("greet", {"name": "Marvin", "extra": "abc"})

    async def test_proxy_can_list_overwritten_tool(self, proxy_server):
        """
        Test that a tool defined on the proxy is listed instead of the proxied tool
        """

        @proxy_server.tool
        def greet(name: str, extra: str = "extra") -> str:
            return f"Overwritten, {name}! {extra}"

        async with Client(proxy_server) as client:
            tools = await client.list_tools()
            greet_tool = next(t for t in tools if t.name == "greet")
            assert "extra" in greet_tool.inputSchema["properties"]

    async def test_proxy_can_list_overwritten_tool_if_disabled(self, proxy_server):
        """
        Test that a tool defined on the proxy is not listed if it is disabled,
        and it doesn't fall back to the proxied tool with the same name
        """

        @proxy_server.tool(enabled=False)
        def greet(name: str, extra: str = "extra") -> str:
            return f"Overwritten, {name}! {extra}"

        async with Client(proxy_server) as client:
            tools = await client.list_tools()
            assert not any(t.name == "greet" for t in tools)


class TestResources:
    async def test_get_resources(self, proxy_server):
        resources = await proxy_server.get_resources()
        assert [r.uri for r in resources.values()] == Contains(
            AnyUrl("data://users"),
            AnyUrl("resource://wave"),
        )
        assert [r.name for r in resources.values()] == Contains("get_users", "wave")

    async def test_list_resources_same_as_original(self, fastmcp_server, proxy_server):
        assert (
            await proxy_server._mcp_list_resources()
            == await fastmcp_server._mcp_list_resources()
        )

    async def test_read_resource(self, proxy_server: FastMCPProxy):
        async with Client(proxy_server) as client:
            result = await client.read_resource("resource://wave")
        assert result[0].text == "👋"  # type: ignore[attr-defined]

    async def test_read_resource_same_as_original(self, fastmcp_server, proxy_server):
        async with Client(fastmcp_server) as client:
            result = await client.read_resource("resource://wave")
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("resource://wave")
        assert proxy_result == result

    async def test_read_json_resource(self, proxy_server: FastMCPProxy):
        async with Client(proxy_server) as client:
            result = await client.read_resource("data://users")
        assert json.loads(result[0].text) == USERS  # type: ignore[attr-defined]

    async def test_read_resource_returns_none_if_not_found(self, proxy_server):
        with pytest.raises(
            McpError, match="Unknown resource: 'resource://nonexistent'"
        ):
            async with Client(proxy_server) as client:
                await client.read_resource("resource://nonexistent")

    async def test_proxy_can_overwrite_proxied_resource(self, proxy_server):
        """
        Test that a resource defined on the proxy can overwrite the proxied resource with the same URI.
        """

        @proxy_server.resource(uri="resource://wave")
        def overwritten_wave() -> str:
            return "Overwritten wave! 🌊"

        async with Client(proxy_server) as client:
            result = await client.read_resource("resource://wave")
        assert result[0].text == "Overwritten wave! 🌊"  # type: ignore[attr-defined]

    async def test_proxy_errors_if_overwritten_resource_is_disabled(self, proxy_server):
        """
        Test that a resource defined on the proxy is not accessible if it is disabled,
        and it doesn't fall back to the proxied resource with the same URI
        """

        @proxy_server.resource(uri="resource://wave", enabled=False)
        def overwritten_wave() -> str:
            return "Overwritten wave! 🌊"

        async with Client(proxy_server) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("resource://wave")

    async def test_proxy_can_list_overwritten_resource(self, proxy_server):
        """
        Test that a resource defined on the proxy is listed instead of the proxied resource
        """

        @proxy_server.resource(uri="resource://wave", name="overwritten_wave")
        def overwritten_wave() -> str:
            return "Overwritten wave! 🌊"

        async with Client(proxy_server) as client:
            resources = await client.list_resources()
            wave_resource = next(
                r for r in resources if str(r.uri) == "resource://wave"
            )
            assert wave_resource.name == "overwritten_wave"

    async def test_proxy_can_list_overwritten_resource_if_disabled(self, proxy_server):
        """
        Test that a resource defined on the proxy is not listed if it is disabled,
        and it doesn't fall back to the proxied resource with the same URI
        """

        @proxy_server.resource(uri="resource://wave", enabled=False)
        def overwritten_wave() -> str:
            return "Overwritten wave! 🌊"

        async with Client(proxy_server) as client:
            resources = await client.list_resources()
            wave_resources = [r for r in resources if str(r.uri) == "resource://wave"]
            assert len(wave_resources) == 0


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
        assert json.loads(result[0].text) == USERS[id - 1]  # type: ignore[attr-defined]

    async def test_read_resource_template_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        async with Client(fastmcp_server) as client:
            result = await client.read_resource("data://user/1")
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("data://user/1")
        assert proxy_result == result

    async def test_proxy_can_overwrite_proxied_resource_template(self, proxy_server):
        """
        Test that a resource template defined on the proxy can overwrite the proxied template with the same URI template.
        """

        @proxy_server.resource(uri="data://user/{user_id}", name="overwritten_get_user")
        def overwritten_get_user(user_id: str) -> dict[str, Any]:
            return {
                "id": user_id,
                "name": "Overwritten User",
                "active": True,
                "extra": "data",
            }

        async with Client(proxy_server) as client:
            result = await client.read_resource("data://user/1")
        user_data = json.loads(result[0].text)  # type: ignore[attr-defined]
        assert user_data["name"] == "Overwritten User"
        assert user_data["extra"] == "data"

    async def test_proxy_errors_if_overwritten_resource_template_is_disabled(
        self, proxy_server
    ):
        """
        Test that a resource template defined on the proxy is not accessible if it is disabled,
        and it doesn't fall back to the proxied template with the same URI template
        """

        @proxy_server.resource(uri="data://user/{user_id}", enabled=False)
        def overwritten_get_user(user_id: str) -> dict[str, Any]:
            return {"id": user_id, "name": "Overwritten User", "active": True}

        async with Client(proxy_server) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("data://user/1")

    async def test_proxy_can_list_overwritten_resource_template(self, proxy_server):
        """
        Test that a resource template defined on the proxy is listed instead of the proxied template
        """

        @proxy_server.resource(uri="data://user/{user_id}", name="overwritten_get_user")
        def overwritten_get_user(user_id: str) -> dict[str, Any]:
            return {"id": user_id, "name": "Overwritten User", "active": True}

        async with Client(proxy_server) as client:
            templates = await client.list_resource_templates()
            user_template = next(
                t for t in templates if t.uriTemplate == "data://user/{user_id}"
            )
            assert user_template.name == "overwritten_get_user"

    async def test_proxy_can_list_overwritten_resource_template_if_disabled(
        self, proxy_server
    ):
        """
        Test that a resource template defined on the proxy is not listed if it is disabled,
        and it doesn't fall back to the proxied template with the same URI template
        """

        @proxy_server.resource(uri="data://user/{user_id}", enabled=False)
        def overwritten_get_user(user_id: str) -> dict[str, Any]:
            return {"id": user_id, "name": "Overwritten User", "active": True}

        async with Client(proxy_server) as client:
            templates = await client.list_resource_templates()
            user_templates = [
                t for t in templates if t.uriTemplate == "data://user/{user_id}"
            ]
            assert len(user_templates) == 0


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
        assert result.messages[0].role == "user"
        assert result.messages[0].content.text == "Welcome to FastMCP, Alice!"  # type: ignore[attr-defined]

    async def test_proxy_can_overwrite_proxied_prompt(self, proxy_server):
        """
        Test that a prompt defined on the proxy can overwrite the proxied prompt with the same name.
        """

        @proxy_server.prompt
        def welcome(name: str, extra: str = "friend") -> str:
            return f"Overwritten welcome, {name}! You are my {extra}."

        async with Client(proxy_server) as client:
            result = await client.get_prompt(
                "welcome", {"name": "Alice", "extra": "colleague"}
            )
        assert result.messages[0].role == "user"
        assert (
            result.messages[0].content.text  # type: ignore[attr-defined]
            == "Overwritten welcome, Alice! You are my colleague."
        )

    async def test_proxy_errors_if_overwritten_prompt_is_disabled(self, proxy_server):
        """
        Test that a prompt defined on the proxy is not accessible if it is disabled,
        and it doesn't fall back to the proxied prompt with the same name
        """

        @proxy_server.prompt(enabled=False)
        def welcome(name: str, extra: str = "friend") -> str:
            return f"Overwritten welcome, {name}! You are my {extra}."

        async with Client(proxy_server) as client:
            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("welcome", {"name": "Alice"})

    async def test_proxy_can_list_overwritten_prompt(self, proxy_server):
        """
        Test that a prompt defined on the proxy is listed instead of the proxied prompt
        """

        @proxy_server.prompt
        def welcome(name: str, extra: str = "friend") -> str:
            return f"Overwritten welcome, {name}! You are my {extra}."

        async with Client(proxy_server) as client:
            prompts = await client.list_prompts()
            welcome_prompt = next(p for p in prompts if p.name == "welcome")
            # Check that the overwritten prompt has the additional 'extra' parameter
            param_names = [arg.name for arg in welcome_prompt.arguments or []]
            assert "extra" in param_names

    async def test_proxy_can_list_overwritten_prompt_if_disabled(self, proxy_server):
        """
        Test that a prompt defined on the proxy is not listed if it is disabled,
        and it doesn't fall back to the proxied prompt with the same name
        """

        @proxy_server.prompt(enabled=False)
        def welcome(name: str, extra: str = "friend") -> str:
            return f"Overwritten welcome, {name}! You are my {extra}."

        async with Client(proxy_server) as client:
            prompts = await client.list_prompts()
            welcome_prompts = [p for p in prompts if p.name == "welcome"]
            assert len(welcome_prompts) == 0


async def test_proxy_handles_multiple_concurrent_tasks_correctly(
    proxy_server: FastMCPProxy,
):
    results = {}

    async def get_and_store(name, coro):
        results[name] = await coro()

    async with create_task_group() as tg:
        tg.start_soon(get_and_store, "prompts", proxy_server.get_prompts)
        tg.start_soon(get_and_store, "resources", proxy_server.get_resources)
        tg.start_soon(get_and_store, "tools", proxy_server.get_tools)

    assert list(results) == Contains("resources", "prompts", "tools")
    assert list(results["prompts"]) == Contains("welcome")
    assert [r.uri for r in results["resources"].values()] == Contains(
        AnyUrl("data://users"),
        AnyUrl("resource://wave"),
    )
    assert [r.name for r in results["resources"].values()] == Contains(
        "get_users", "wave"
    )
    assert list(results["tools"]) == Contains(
        "greet", "add", "error_tool", "tool_without_description"
    )
