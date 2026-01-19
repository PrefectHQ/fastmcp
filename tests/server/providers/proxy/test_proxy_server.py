import inspect
import json
from typing import Any, cast

import mcp.types as mcp_types
import pytest
from anyio import create_task_group
from dirty_equals import Contains
from mcp import McpError
from mcp.types import Icon, TextContent, TextResourceContents
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
)
from fastmcp.tools.tool import ToolResult
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
)

USERS = [
    {"id": "1", "name": "Alice", "active": True},
    {"id": "2", "name": "Bob", "active": True},
    {"id": "3", "name": "Charlie", "active": False},
]


@pytest.fixture
def fastmcp_server():
    server = FastMCP("TestServer")

    # --- Tools ---

    @server.tool(
        tags={"greet"},
        title="Greet",
        icons=[Icon(src="https://example.com/greet-icon.png")],
    )
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

    @server.resource(
        uri="resource://wave",
        tags={"wave"},
        title="Wave",
        icons=[Icon(src="https://example.com/wave-icon.png")],
    )
    def wave() -> str:
        return "👋"

    @server.resource(uri="data://users")
    async def get_users() -> str:
        import json

        return json.dumps(USERS, separators=(",", ":"))

    @server.resource(
        uri="data://user/{user_id}",
        tags={"users"},
        title="User Template",
        icons=[Icon(src="https://example.com/user-icon.png")],
    )
    async def get_user(user_id: str) -> str:
        import json

        user = next((user for user in USERS if user["id"] == user_id), None)
        return json.dumps(user, separators=(",", ":")) if user else "null"

    @server.resource(uri="data://multi")
    def get_multi_content() -> ResourceResult:
        """Resource that returns multiple content items."""
        return ResourceResult(
            contents=[
                ResourceContent(content="First item", mime_type="text/plain"),
                ResourceContent(
                    content='{"key": "value"}', mime_type="application/json"
                ),
                ResourceContent(
                    content="# Markdown\nContent", mime_type="text/markdown"
                ),
            ],
            meta={"count": 3},
        )

    @server.resource(uri="data://multi/{id}")
    def get_multi_template(id: str) -> ResourceResult:
        """Resource template that returns multiple content items."""
        return ResourceResult(
            contents=[
                ResourceContent(content=f"Item {id} - First", mime_type="text/plain"),
                ResourceContent(
                    content=f'{{"id": "{id}", "status": "active"}}',
                    mime_type="application/json",
                ),
            ],
            meta={"id": id},
        )

    # --- Prompts ---

    @server.prompt(
        tags={"welcome"},
        title="Welcome",
        icons=[Icon(src="https://example.com/welcome-icon.png")],
    )
    def welcome(name: str) -> str:
        return f"Welcome to FastMCP, {name}!"

    return server


@pytest.fixture
async def proxy_server(fastmcp_server):
    """Fixture that creates a FastMCP proxy server."""
    return create_proxy(ProxyClient(transport=FastMCPTransport(fastmcp_server)))


async def test_create_proxy_with_client(fastmcp_server):
    """Test create_proxy with a Client."""
    client = ProxyClient(transport=FastMCPTransport(fastmcp_server))
    server = create_proxy(client)

    assert isinstance(server, FastMCPProxy)
    assert isinstance(server, FastMCP)
    assert server.name.startswith("FastMCPProxy-")


async def test_create_proxy_with_server(fastmcp_server):
    """create_proxy should accept a FastMCP instance."""
    proxy = create_proxy(fastmcp_server)
    async with Client(proxy) as client:
        result = await client.call_tool("greet", {"name": "Test"})
        assert result.data == "Hello, Test!"


async def test_create_proxy_with_transport(fastmcp_server):
    """create_proxy should accept a ClientTransport."""
    proxy = create_proxy(FastMCPTransport(fastmcp_server))
    async with Client(proxy) as client:
        result = await client.call_tool("greet", {"name": "Test"})
        assert result.data == "Hello, Test!"


def test_create_proxy_with_url():
    """create_proxy should accept a URL without connecting."""
    proxy = create_proxy("http://example.com/mcp/")
    assert isinstance(proxy, FastMCPProxy)
    client = cast(Client, proxy.client_factory())
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.url == "http://example.com/mcp/"


# --- Deprecated as_proxy tests (verify backwards compatibility) ---


async def test_as_proxy_deprecated_with_server(fastmcp_server):
    """FastMCP.as_proxy should work but emit deprecation warning."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        proxy = FastMCP.as_proxy(fastmcp_server)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "create_proxy" in str(w[0].message)

    async with Client(proxy) as client:
        result = await client.call_tool("greet", {"name": "Test"})
        assert result.data == "Hello, Test!"


def test_as_proxy_deprecated_with_url():
    """FastMCP.as_proxy should work but emit deprecation warning."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        proxy = FastMCP.as_proxy("http://example.com/mcp/")
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)

    assert isinstance(proxy, FastMCPProxy)


async def test_proxy_with_async_client_factory():
    """FastMCPProxy should accept an async client_factory."""

    async def async_factory():
        return Client("http://example.com/mcp/")

    proxy = FastMCPProxy(client_factory=async_factory)
    assert isinstance(proxy, FastMCPProxy)
    assert inspect.iscoroutinefunction(proxy.client_factory)
    client = proxy.client_factory()
    if inspect.isawaitable(client):
        client = await client
    assert isinstance(client, Client)
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.url == "http://example.com/mcp/"


class TestTools:
    async def test_get_tools(self, proxy_server):
        tools = await proxy_server.list_tools()
        assert any(t.name == "greet" for t in tools)
        assert any(t.name == "add" for t in tools)
        assert any(t.name == "error_tool" for t in tools)
        assert any(t.name == "tool_without_description" for t in tools)

    async def test_get_tools_meta(self, proxy_server):
        tools = await proxy_server.list_tools()
        greet_tool = next(t for t in tools if t.name == "greet")
        assert greet_tool.title == "Greet"
        assert greet_tool.meta == {"fastmcp": {"tags": ["greet"]}}
        assert greet_tool.icons == [Icon(src="https://example.com/greet-icon.png")]

    async def test_get_transformed_tools(self):
        """Test that tool transformations are applied to proxied tools."""
        from fastmcp.server.transforms import ToolTransform

        # Create server with transformation
        server = FastMCP("TestServer")

        @server.tool
        def add(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        server.add_transform(
            ToolTransform({"add": ToolTransformConfig(name="add_transformed")})
        )

        proxy = create_proxy(server)
        tools = await proxy.list_tools()
        assert any(t.name == "add_transformed" for t in tools)
        assert not any(t.name == "add" for t in tools)

    async def test_call_transformed_tools(self):
        """Test calling a transformed tool through a proxy."""
        from fastmcp.server.transforms import ToolTransform

        # Create server with transformation
        server = FastMCP("TestServer")

        @server.tool
        def add(a: int, b: int) -> int:
            """Add two numbers together."""
            return a + b

        server.add_transform(
            ToolTransform({"add": ToolTransformConfig(name="add_transformed")})
        )

        proxy = create_proxy(server)
        async with Client(proxy) as client:
            result = await client.call_tool("add_transformed", {"a": 1, "b": 2})
        assert result.data == 3

    async def test_tool_without_description(self, proxy_server):
        tools = await proxy_server.list_tools()
        tool = next(t for t in tools if t.name == "tool_without_description")
        assert tool.description is None

    async def test_list_tools_same_as_original(self, fastmcp_server, proxy_server):
        assert await proxy_server._list_tools_mcp(
            mcp_types.ListToolsRequest()
        ) == await fastmcp_server._list_tools_mcp(mcp_types.ListToolsRequest())

    async def test_call_tool_result_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        result = await fastmcp_server._call_tool_mcp("greet", {"name": "Alice"})
        proxy_result = await proxy_server._call_tool_mcp("greet", {"name": "Alice"})

        assert result == proxy_result

    async def test_call_tool_calls_tool(self, proxy_server):
        async with Client(proxy_server) as client:
            proxy_result = await client.call_tool("add", {"a": 1, "b": 2})
        assert proxy_result.data == 3

    async def test_error_tool_raises_error(self, proxy_server):
        with pytest.raises(ToolError, match="This is a test error"):
            async with Client(proxy_server) as client:
                await client.call_tool("error_tool", {})

    async def test_call_tool_forwards_meta(self, fastmcp_server, proxy_server):
        """Test that metadata from proxied tool results is properly forwarded."""

        @fastmcp_server.tool
        def tool_with_meta(value: str) -> ToolResult:
            """A tool that returns metadata in its result."""
            return ToolResult(
                content=f"Result: {value}",
                meta={"custom_key": "custom_value", "processed": True},
            )

        async with Client(proxy_server) as client:
            result = await client.call_tool("tool_with_meta", {"value": "test"})

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Result: test"
        assert result.meta == {"custom_key": "custom_value", "processed": True}

    async def test_proxy_can_overwrite_proxied_tool(self, proxy_server):
        """
        Test that a tool defined on the proxy can overwrite the proxied tool with the same name.
        """

        @proxy_server.tool
        def greet(name: str, extra: str = "extra") -> str:
            return f"Overwritten, {name}! {extra}"

        async with Client(proxy_server) as client:
            result = await client.call_tool("greet", {"name": "Marvin", "extra": "abc"})
        assert result.data == "Overwritten, Marvin! abc"

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


class TestResources:
    async def test_get_resources(self, proxy_server):
        resources = await proxy_server.list_resources()
        assert [r.uri for r in resources] == Contains(
            AnyUrl("data://users"),
            AnyUrl("resource://wave"),
        )
        assert [r.name for r in resources] == Contains("get_users", "wave")

    async def test_get_resources_meta(self, proxy_server):
        resources = await proxy_server.list_resources()
        wave_resource = next(r for r in resources if str(r.uri) == "resource://wave")
        assert wave_resource.title == "Wave"
        assert wave_resource.meta == {"fastmcp": {"tags": ["wave"]}}
        assert wave_resource.icons == [Icon(src="https://example.com/wave-icon.png")]

    async def test_list_resources_same_as_original(self, fastmcp_server, proxy_server):
        assert await proxy_server._list_resources_mcp(
            mcp_types.ListResourcesRequest()
        ) == await fastmcp_server._list_resources_mcp(mcp_types.ListResourcesRequest())

    async def test_read_resource(self, proxy_server: FastMCPProxy):
        async with Client(proxy_server) as client:
            result = await client.read_resource("resource://wave")
        assert isinstance(result[0], TextResourceContents)
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
        assert len(result) == 1
        assert isinstance(result[0], TextResourceContents)
        # The resource returns all users serialized as JSON
        users = json.loads(result[0].text)
        assert users == USERS

    async def test_proxy_returns_all_resource_contents(
        self, fastmcp_server, proxy_server
    ):
        """Test that proxy correctly returns all resource contents, not just the first one."""
        # Read from original server
        async with Client(fastmcp_server) as client:
            original_result = await client.read_resource("data://multi")

        # Read from proxy server
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("data://multi")

        # Both should return the same number of contents
        assert len(original_result) == len(proxy_result)
        assert len(original_result) == 3

        # Verify all contents match
        for i, (original, proxied) in enumerate(zip(original_result, proxy_result)):
            assert isinstance(original, TextResourceContents)
            assert isinstance(proxied, TextResourceContents)
            assert original.text == proxied.text, f"Content {i} text mismatch"
            assert original.mimeType == proxied.mimeType, (
                f"Content {i} mimeType mismatch"
            )
            assert original.meta == proxied.meta, f"Content {i} meta mismatch"

        # Verify the contents are what we expect
        assert original_result[0].text == "First item"
        assert original_result[0].mimeType == "text/plain"
        assert original_result[1].text == '{"key": "value"}'
        assert original_result[1].mimeType == "application/json"
        assert original_result[2].text == "# Markdown\nContent"
        assert original_result[2].mimeType == "text/markdown"

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
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Overwritten wave! 🌊"

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


class TestResourceTemplates:
    async def test_get_resource_templates(self, proxy_server):
        templates = await proxy_server.list_resource_templates()
        assert [t.name for t in templates] == Contains("get_user")

    async def test_get_resource_templates_meta(self, proxy_server):
        templates = await proxy_server.list_resource_templates()
        get_user_template = next(
            t for t in templates if t.uri_template == "data://user/{user_id}"
        )
        assert get_user_template.title == "User Template"
        assert get_user_template.meta == {"fastmcp": {"tags": ["users"]}}
        assert get_user_template.icons == [
            Icon(src="https://example.com/user-icon.png")
        ]

    async def test_list_resource_templates_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        result = await fastmcp_server._list_resource_templates_mcp(
            mcp_types.ListResourceTemplatesRequest()
        )
        proxy_result = await proxy_server._list_resource_templates_mcp(
            mcp_types.ListResourceTemplatesRequest()
        )
        assert proxy_result == result

    @pytest.mark.parametrize("id", [1, 2, 3])
    async def test_read_resource_template(self, proxy_server: FastMCPProxy, id: int):
        async with Client(proxy_server) as client:
            result = await client.read_resource(f"data://user/{id}")
        assert isinstance(result[0], TextResourceContents)
        assert json.loads(result[0].text) == USERS[id - 1]

    async def test_read_resource_template_same_as_original(
        self, fastmcp_server, proxy_server
    ):
        async with Client(fastmcp_server) as client:
            result = await client.read_resource("data://user/1")
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("data://user/1")
        assert proxy_result == result

    async def test_proxy_template_returns_all_resource_contents(
        self, fastmcp_server, proxy_server
    ):
        """Test that proxy template correctly returns all resource contents."""
        # Read from original server
        async with Client(fastmcp_server) as client:
            original_result = await client.read_resource("data://multi/test123")

        # Read from proxy server
        async with Client(proxy_server) as client:
            proxy_result = await client.read_resource("data://multi/test123")

        # Both should return the same number of contents
        assert len(original_result) == len(proxy_result)
        assert len(original_result) == 2

        # Verify all contents match
        for i, (original, proxied) in enumerate(zip(original_result, proxy_result)):
            assert isinstance(original, TextResourceContents)
            assert isinstance(proxied, TextResourceContents)
            assert original.text == proxied.text, f"Content {i} text mismatch"
            assert original.mimeType == proxied.mimeType, (
                f"Content {i} mimeType mismatch"
            )

        # Verify the contents are what we expect
        assert original_result[0].text == "Item test123 - First"
        assert original_result[0].mimeType == "text/plain"
        assert original_result[1].text == '{"id": "test123", "status": "active"}'
        assert original_result[1].mimeType == "application/json"

    async def test_proxy_can_overwrite_proxied_resource_template(self, proxy_server):
        """
        Test that a resource template defined on the proxy can overwrite the proxied template with the same URI template.
        """

        @proxy_server.resource(uri="data://user/{user_id}", name="overwritten_get_user")
        def overwritten_get_user(user_id: str) -> str:
            return json.dumps(
                {
                    "id": user_id,
                    "name": "Overwritten User",
                    "active": True,
                    "extra": "data",
                }
            )

        async with Client(proxy_server) as client:
            result = await client.read_resource("data://user/1")
        assert isinstance(result[0], TextResourceContents)
        user_data = json.loads(result[0].text)
        assert user_data["name"] == "Overwritten User"
        assert user_data["extra"] == "data"

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


class TestPrompts:
    async def test_get_prompts_server_method(self, proxy_server: FastMCPProxy):
        prompts = await proxy_server.list_prompts()
        assert [p.name for p in prompts] == Contains("welcome")

    async def test_get_prompts_meta(self, proxy_server):
        prompts = await proxy_server.list_prompts()
        welcome_prompt = next(p for p in prompts if p.name == "welcome")
        assert welcome_prompt.title == "Welcome"
        assert welcome_prompt.meta == {"fastmcp": {"tags": ["welcome"]}}
        assert welcome_prompt.icons == [
            Icon(src="https://example.com/welcome-icon.png")
        ]

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
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Welcome to FastMCP, Alice!"

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
        assert isinstance(result.messages[0].content, TextContent)
        assert (
            result.messages[0].content.text
            == "Overwritten welcome, Alice! You are my colleague."
        )

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


async def test_proxy_handles_multiple_concurrent_tasks_correctly(
    proxy_server: FastMCPProxy,
):
    results = {}

    async def get_and_store(name, coro):
        results[name] = await coro()

    async with create_task_group() as tg:
        tg.start_soon(get_and_store, "prompts", proxy_server.list_prompts)
        tg.start_soon(get_and_store, "resources", proxy_server.list_resources)
        tg.start_soon(get_and_store, "tools", proxy_server.list_tools)

    assert list(results) == Contains("resources", "prompts", "tools")
    assert [p.name for p in results["prompts"]] == Contains("welcome")
    assert [r.uri for r in results["resources"]] == Contains(
        AnyUrl("data://users"),
        AnyUrl("resource://wave"),
    )
    assert [r.name for r in results["resources"]] == Contains("get_users", "wave")
    assert [t.name for t in results["tools"]] == Contains(
        "greet", "add", "error_tool", "tool_without_description"
    )


class TestProxyComponentEnableDisable:
    """Test that enable/disable on proxy components guides users to server-level methods."""

    async def test_proxy_tool_enable_raises_not_implemented(self, proxy_server):
        """Test that enable() on proxy tools raises NotImplementedError."""
        tools = await proxy_server.list_tools()
        tool = next(t for t in tools if t.name == "greet")

        with pytest.raises(NotImplementedError, match="server.enable"):
            tool.enable()

    async def test_proxy_tool_disable_raises_not_implemented(self, proxy_server):
        """Test that disable() on proxy tools raises NotImplementedError."""
        tools = await proxy_server.list_tools()
        tool = next(t for t in tools if t.name == "greet")

        with pytest.raises(NotImplementedError, match="server.disable"):
            tool.disable()

    async def test_proxy_resource_enable_raises_not_implemented(self, proxy_server):
        """Test that enable() on proxy resources raises NotImplementedError."""
        resources = await proxy_server.list_resources()
        resource = next(r for r in resources if str(r.uri) == "resource://wave")

        with pytest.raises(NotImplementedError, match="server.enable"):
            resource.enable()

    async def test_proxy_resource_disable_raises_not_implemented(self, proxy_server):
        """Test that disable() on proxy resources raises NotImplementedError."""
        resources = await proxy_server.list_resources()
        resource = next(r for r in resources if str(r.uri) == "resource://wave")

        with pytest.raises(NotImplementedError, match="server.disable"):
            resource.disable()

    async def test_proxy_prompt_enable_raises_not_implemented(self, proxy_server):
        """Test that enable() on proxy prompts raises NotImplementedError."""
        prompts = await proxy_server.list_prompts()
        prompt = next(p for p in prompts if p.name == "welcome")

        with pytest.raises(NotImplementedError, match="server.enable"):
            prompt.enable()

    async def test_proxy_prompt_disable_raises_not_implemented(self, proxy_server):
        """Test that disable() on proxy prompts raises NotImplementedError."""
        prompts = await proxy_server.list_prompts()
        prompt = next(p for p in prompts if p.name == "welcome")

        with pytest.raises(NotImplementedError, match="server.disable"):
            prompt.disable()
