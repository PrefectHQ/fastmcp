import inspect
import json
import time
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx2
import mcp_types
import pytest
from anyio import create_task_group
from dirty_equals import Contains
from mcp import MCPError
from mcp_types import Icon, TextContent, TextResourceContents
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import AnyUrl

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from fastmcp.client.transports.base import TransportOptions
from fastmcp.exceptions import ToolError
from fastmcp.mcp_config import MCPConfig
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server import create_proxy
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
    ProxyProvider,
    _ForwardingClientSession,
)
from fastmcp.tools.base import ToolResult
from fastmcp.tools.tool_transform import (
    ToolTransformConfig,
)
from fastmcp.utilities.http import find_available_port
from fastmcp.utilities.tests import run_server_async

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

    @server.prompt
    def image_prompt():
        """A prompt that returns an image."""
        from fastmcp.prompts.base import Message, PromptResult

        return PromptResult(
            messages=[
                Message("Here is an image:"),
                Message(
                    content=mcp_types.ImageContent(
                        type="image",
                        data="iVBORw0KGgoAAAANSUhEUg==",
                        mime_type="image/png",
                    ),
                    role="user",
                ),
            ]
        )

    return server


@pytest.fixture
async def proxy_server(fastmcp_server):
    """Fixture that creates a FastMCP proxy server.

    Passing an already-constructed `ProxyClient` as the target (rather than a
    raw `FastMCP`/URL/etc.) means `create_proxy` reuses that client as-is
    instead of building one through the era-mirroring factory — so this
    backend stays pinned to `ProxyClient`'s own default of `mode="legacy"`
    regardless of what era the front client negotiates. A test that actually
    forwards a tool *call* through this fixture (not just a listing) needs
    its own front `Client` pinned to `mode="legacy"` too: otherwise a modern
    front's request `_meta` carries the reserved modern-envelope keys, which
    `ProxyTool.run`'s legacy-backend path forwards verbatim onto this
    legacy-locked backend session, and the backend server rejects it as a
    protocol violation.
    """
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


async def test_proxy_forwards_upstream_instructions():
    """A proxy should surface the upstream server's instructions in the handshake.

    `FastMCPProxy` registers a `server/discover` handler that forwards the
    upstream's instructions, mirroring what `ProxyInitializeMiddleware.on_initialize`
    already does for the legacy handshake, so `client.session.instructions`
    (era-neutral) resolves the same way on both protocol eras.
    """
    upstream = FastMCP(name="upstream", instructions="USE_THIS_MARKER_123")
    proxy = create_proxy(upstream, name="proxy")

    async with Client(proxy) as client:
        assert client.session.instructions == "USE_THIS_MARKER_123"


async def test_proxy_own_instructions_take_precedence():
    """Instructions explicitly set on the proxy override the upstream's."""
    upstream = FastMCP(name="upstream", instructions="upstream instructions")
    proxy = create_proxy(upstream, name="proxy", instructions="proxy instructions")

    async with Client(proxy) as client:
        assert client.session.instructions == "proxy instructions"


async def test_proxy_instructions_none_when_upstream_has_none():
    """A proxy over an upstream without instructions reports no instructions."""
    upstream = FastMCP(name="upstream")
    proxy = create_proxy(upstream, name="proxy")

    async with Client(proxy) as client:
        assert client.session.instructions is None


def test_create_proxy_with_url():
    """create_proxy should accept a URL without connecting."""
    proxy = create_proxy("http://example.com/mcp/")
    assert isinstance(proxy, FastMCPProxy)
    client = cast(Client, proxy.client_factory())
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.url == "http://example.com/mcp/"


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


async def test_proxy_ping_forwards_to_remote_server(fastmcp_server):
    proxy = create_proxy(fastmcp_server)

    async with Client(proxy, mode="legacy") as client:
        assert await client.ping() is True


async def test_proxy_ping_surfaces_wrong_remote_path():
    remote = FastMCP("remote")
    async with run_server_async(remote, transport="http") as url:
        proxy = create_proxy(StreamableHttpTransport(url.removesuffix("/mcp")))

        # This asserts the error surfaces from merely *connecting* to the proxy,
        # with no operation performed. That only happens on the legacy handshake:
        # `ProxyInitializeMiddleware.on_initialize` eagerly probes the backend
        # during the front's own `initialize` call. A modern front negotiates
        # `server/discover` instead, which never runs that middleware hook, so
        # connecting succeeds regardless of backend health and the failure would
        # only surface on first real use. Pinned because the subject here is
        # that eager, handshake-time probe.
        #
        # SDK v2 surfaces a wrong remote path as an HTTP "Not Found" rather than
        # the v1 "Session terminated" message.
        with pytest.raises(MCPError, match="Not Found"):
            async with Client(proxy, mode="legacy"):
                pass


async def test_proxy_initialize_forwards_remote_connection_error():
    port = find_available_port()
    proxy = create_proxy(
        StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp"),
        provider_error_strategy="raise",
    )

    # Same reasoning as test_proxy_ping_surfaces_wrong_remote_path above: the
    # error surfaces from connecting alone only via the legacy handshake's
    # eager backend probe in `ProxyInitializeMiddleware.on_initialize`.
    with pytest.raises(MCPError, match="Client failed to connect"):
        async with Client(proxy, mode="legacy"):
            pass


async def test_proxy_list_tools_surfaces_remote_connection_error():
    """A dead backend surfaces as an MCPError naming the connection failure.

    The provider normalizes transport failures into `MCPError` (rather than
    letting the client's `RuntimeError` escape) so the error survives the
    modern era's wire boundary, which masks any non-MCPError as a generic
    "Internal server error".
    """
    port = find_available_port()
    proxy = create_proxy(
        StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp"),
        provider_error_strategy="raise",
    )

    with pytest.raises(MCPError, match="Client failed to connect"):
        await proxy.list_tools()


async def test_proxy_list_tools_client_surfaces_remote_connection_error():
    """With a modern front, connecting succeeds (no eager backend probe — see
    test_proxy_ping_surfaces_wrong_remote_path) and the failure only surfaces
    once `list_tools()` actually hits the dead backend. `ProxyProvider._list_tools`
    now normalizes the raw `httpx2.ConnectError` from the failed backend connect
    into the `MCPError("Client failed to connect...")` this test expects, the
    same way `ProxyInitializeMiddleware.on_initialize` and `ProxyTool.run`
    already did.
    """
    port = find_available_port()
    proxy = create_proxy(
        StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp"),
        provider_error_strategy="raise",
    )

    with pytest.raises(MCPError, match="Client failed to connect"):
        async with Client(proxy) as client:
            await client.list_tools()


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
        async with Client(fastmcp_server) as original_client:
            original = await original_client.list_tools()
        async with Client(proxy_server) as proxy_client:
            proxied = await proxy_client.list_tools()
        assert proxied == original

    async def test_call_tool_result_same_as_original(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        # proxy_server's backend is pinned to legacy (see its fixture docstring);
        # match the front so a real tool call doesn't cross eras.
        async with Client(fastmcp_server) as original_client:
            result = await original_client.call_tool("greet", {"name": "Alice"})
        async with Client(proxy_server, mode="legacy") as proxy_client:
            proxy_result = await proxy_client.call_tool("greet", {"name": "Alice"})

        assert result.content == proxy_result.content
        assert result.data == proxy_result.data

    async def test_call_tool_calls_tool(self, proxy_server):
        # See proxy_server fixture docstring: its backend is pinned to legacy.
        async with Client(proxy_server, mode="legacy") as client:
            proxy_result = await client.call_tool("add", {"a": 1, "b": 2})
        assert proxy_result.data == 3

    async def test_error_tool_raises_error(self, proxy_server):
        # See proxy_server fixture docstring: its backend is pinned to legacy.
        with pytest.raises(ToolError, match="This is a test error"):
            async with Client(proxy_server, mode="legacy") as client:
                await client.call_tool("error_tool", {})

    async def test_error_tool_with_image_content(self, proxy_server):
        """Non-TextContent error responses should not crash with AttributeError."""
        error_result = mcp_types.CallToolResult(
            content=[
                mcp_types.ImageContent(
                    type="image", data="abc123", mime_type="image/png"
                )
            ],
            is_error=True,
        )
        with patch.object(
            Client, "call_tool_mcp", new_callable=AsyncMock, return_value=error_result
        ):
            with pytest.raises(ToolError):
                async with Client(proxy_server) as client:
                    await client.call_tool("error_tool", {})

    async def test_error_tool_with_empty_content(self, proxy_server):
        """Error responses with empty content should not crash."""
        error_result = mcp_types.CallToolResult(
            content=[],
            is_error=True,
        )
        with patch.object(
            Client, "call_tool_mcp", new_callable=AsyncMock, return_value=error_result
        ):
            with pytest.raises(ToolError):
                async with Client(proxy_server) as client:
                    await client.call_tool("error_tool", {})

    async def test_error_tool_passthrough_preserves_content(self, proxy_server):
        """Upstream error results pass through with content intact, not flattened."""
        error_result = mcp_types.CallToolResult(
            content=[
                mcp_types.ImageContent(
                    type="image", data="abc123", mime_type="image/png"
                )
            ],
            structured_content={"detail": "boom"},
            is_error=True,
        )
        with patch.object(
            Client, "call_tool_mcp", new_callable=AsyncMock, return_value=error_result
        ):
            async with Client(proxy_server) as client:
                result = await client.call_tool("error_tool", {}, raise_on_error=False)

        assert result.is_error is True
        assert isinstance(result.content[0], mcp_types.ImageContent)
        assert result.content[0].data == "abc123"
        assert result.structured_content == {"detail": "boom"}

    async def test_call_tool_forwards_meta(self, fastmcp_server, proxy_server):
        """Test that metadata from proxied tool results is properly forwarded."""

        @fastmcp_server.tool
        def tool_with_meta(value: str) -> ToolResult:
            """A tool that returns metadata in its result."""
            return ToolResult(
                content=f"Result: {value}",
                meta={"custom_key": "custom_value", "processed": True},
            )

        # See proxy_server fixture docstring: its backend is pinned to legacy.
        async with Client(proxy_server, mode="legacy") as client:
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
            assert "extra" in greet_tool.input_schema["properties"]


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
        async with Client(fastmcp_server) as original_client:
            original = await original_client.list_resources()
        async with Client(proxy_server) as proxy_client:
            proxied = await proxy_client.list_resources()
        assert proxied == original

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
            assert original.mime_type == proxied.mime_type, (
                f"Content {i} mimeType mismatch"
            )
            assert original.meta == proxied.meta, f"Content {i} meta mismatch"

        # Verify the contents are what we expect
        assert original_result[0].text == "First item"
        assert original_result[0].mime_type == "text/plain"
        assert original_result[1].text == '{"key": "value"}'
        assert original_result[1].mime_type == "application/json"
        assert original_result[2].text == "# Markdown\nContent"
        assert original_result[2].mime_type == "text/markdown"

    async def test_read_resource_returns_none_if_not_found(self, proxy_server):
        with pytest.raises(
            MCPError, match="Resource not found: 'resource://nonexistent'"
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
        async with Client(fastmcp_server) as original_client:
            result = await original_client.list_resource_templates()
        async with Client(proxy_server) as proxy_client:
            proxy_result = await proxy_client.list_resource_templates()
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
            assert original.mime_type == proxied.mime_type, (
                f"Content {i} mimeType mismatch"
            )

        # Verify the contents are what we expect
        assert original_result[0].text == "Item test123 - First"
        assert original_result[0].mime_type == "text/plain"
        assert original_result[1].text == '{"id": "test123", "status": "active"}'
        assert original_result[1].mime_type == "application/json"

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
                t for t in templates if t.uri_template == "data://user/{user_id}"
            )
            assert user_template.name == "overwritten_get_user"


class TestResourceTemplateQueryParams:
    """Resource templates with RFC 6570 {?param} query params work through proxy."""

    async def test_query_param_forwarded(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}{?format}")
        def get_data(id: str, format: str = "json") -> str:
            return f"id={id} format={format}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://123?format=xml")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=123 format=xml"

    async def test_query_param_default_used_when_omitted(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}{?format}")
        def get_data(id: str, format: str = "json") -> str:
            return f"id={id} format={format}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://123")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=123 format=json"

    async def test_multiple_query_params_forwarded(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}{?limit,offset}")
        def get_data(id: str, limit: int = 10, offset: int = 0) -> str:
            return f"id={id} limit={limit} offset={offset}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://abc?limit=5&offset=20")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=abc limit=5 offset=20"

    async def test_encoded_path_param_preserved(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}")
        def get_data(id: str) -> str:
            return f"id={id}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://a%2Fb")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=a/b"

    async def test_hyphenated_query_param_forwarded(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}{?api-version}")
        def get_data(id: str, api_version: str = "v1") -> str:
            return f"id={id} api_version={api_version}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://123?api-version=v2")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=123 api_version=v2"

    def test_same_name_in_path_and_query_is_rejected(self):
        remote = FastMCP("Remote")
        with pytest.raises(ValueError, match="must be optional"):

            @remote.resource("data://{id}{?id}")
            def get_data(id: str) -> str:
                return id

    async def test_hyphenated_query_param_not_double_encoded(self):
        remote = FastMCP("Remote")

        @remote.resource("data://{id}{?api-version}")
        def get_data(id: str, api_version: str = "v1") -> str:
            return f"id={id} api_version={api_version}"

        proxy = create_proxy(Client(remote))
        async with Client(proxy) as client:
            result = await client.read_resource("data://123?api-version=a%2Fb")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "id=123 api_version=a/b"


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

    async def test_proxy_prompt_preserves_image_content(
        self, fastmcp_server: FastMCP, proxy_server: FastMCPProxy
    ):
        """Test that ProxyPrompt preserves ImageContent without lossy conversion."""
        async with Client(fastmcp_server) as client:
            result = await client.get_prompt("image_prompt")
        async with Client(proxy_server) as client:
            proxy_result = await client.get_prompt("image_prompt")

        # The proxy result should match the original exactly
        assert proxy_result == result
        # Verify the image content is preserved as ImageContent, not JSON text
        assert isinstance(proxy_result.messages[1].content, mcp_types.ImageContent)
        assert proxy_result.messages[1].content.data == "iVBORw0KGgoAAAANSUhEUg=="
        assert proxy_result.messages[1].content.mime_type == "image/png"


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


class TestProxyProviderCache:
    """Tests for the ProxyProvider component list caching."""

    async def test_get_tool_uses_cached_list(self, fastmcp_server):
        """Calling call_tool should resolve from cache after an initial list."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
        )
        # Warm the cache via list
        tools = await provider.list_tools()
        assert any(t.name == "greet" for t in tools)

        # _get_tool should resolve from cache without calling _list_tools again
        with patch.object(
            provider, "_get_client", new_callable=AsyncMock
        ) as mock_client:
            tool = await provider._get_tool("greet")
            assert tool is not None
            assert tool.name == "greet"
            mock_client.assert_not_called()

    async def test_get_tool_fetches_on_cold_cache(self, fastmcp_server):
        """First _get_tool with no prior list should populate the cache."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
        )
        assert provider._tools_cache is None
        tool = await provider._get_tool("greet")
        assert tool is not None
        assert provider._tools_cache is not None

    async def test_cache_expires_after_ttl(self, fastmcp_server):
        """After TTL expires, _get_tool should re-fetch from the backend."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
            cache_ttl=0.0,
        )
        # Warm the cache
        await provider._list_tools()
        # With ttl=0 the cache is immediately stale, so _get_tool must re-fetch
        assert provider._tools_cache is not None
        original_ts = provider._tools_cache.timestamp

        time.sleep(0.05)

        await provider._get_tool("greet")
        assert provider._tools_cache.timestamp > original_ts

    async def test_list_tools_refreshes_cache(self, fastmcp_server):
        """Explicit list_tools always refreshes the cache timestamp."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
        )
        await provider._list_tools()
        first_ts = provider._tools_cache.timestamp  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        # Tiny sleep so monotonic clock advances
        time.sleep(0.05)

        await provider._list_tools()
        assert provider._tools_cache.timestamp > first_ts  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    async def test_cache_ttl_zero_disables_caching(self, fastmcp_server):
        """With cache_ttl=0, every _get_tool call should re-fetch."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
            cache_ttl=0.0,
        )
        # Each _get_tool call should trigger a fresh _list_tools
        call_count = 0
        original_list = provider._list_tools

        async def counting_list():
            nonlocal call_count
            call_count += 1
            return await original_list()

        with patch.object(provider, "_list_tools", side_effect=counting_list):
            await provider._get_tool("greet")
            await provider._get_tool("add")
        assert call_count == 2

    async def test_get_resource_uses_cache(self, fastmcp_server):
        """Resource lookups should also use the cache."""
        provider = ProxyProvider(
            lambda: ProxyClient(FastMCPTransport(fastmcp_server)),
        )
        await provider._list_resources()
        with patch.object(
            provider, "_get_client", new_callable=AsyncMock
        ) as mock_client:
            # Even if no resources match, the cache is used (no backend call)
            await provider._get_resource("config://app")
            mock_client.assert_not_called()

    async def test_call_tool_through_server_uses_cache(self, fastmcp_server):
        """End-to-end: calling a tool on a proxy server should only connect
        for the actual tool execution, not for tool resolution."""
        proxy = create_proxy(fastmcp_server)
        # Warm the cache by listing
        await proxy.list_tools()

        # Now call a tool — the provider's _list_tools should NOT be called
        # because the cache is warm. The connection happens only in ProxyTool.run.
        proxy_provider = next(
            p for p in proxy.providers if isinstance(p, ProxyProvider)
        )
        with patch.object(
            proxy_provider, "_list_tools", wraps=proxy_provider._list_tools
        ) as mock_list:
            result = await proxy.call_tool("greet", {"name": "Alice"})
            mock_list.assert_not_called()
        assert result.content[0].text == "Hello, Alice!"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]


class TestProxySpanAttributes:
    """Regression tests for span attributes on un-renamed proxy components.

    A proxy component's private ``_backend_*`` field is only populated when
    the component is renamed via ``model_copy``. For un-renamed components it
    stays ``None``, so ``get_span_attributes()`` would emit ``None`` for the
    ``fastmcp.proxy.backend_*`` keys — which OpenTelemetry rejects with
    ``Invalid type NoneType for attribute ... value`` and drops, producing
    log spam on every proxied call.
    """

    async def test_proxy_tool_span_attributes_fall_back_to_name(self, proxy_server):
        proxy_provider = next(
            p for p in proxy_server.providers if isinstance(p, ProxyProvider)
        )
        tools = await proxy_provider._list_tools()
        assert tools, "expected the fixture to expose at least one tool"
        for tool in tools:
            attrs = tool.get_span_attributes()
            assert attrs["fastmcp.proxy.backend_name"] == tool.name
            assert all(v is not None for v in attrs.values()), (
                f"OpenTelemetry rejects None attribute values; got {attrs!r}"
            )

    async def test_proxy_resource_span_attributes_fall_back_to_uri(self, proxy_server):
        proxy_provider = next(
            p for p in proxy_server.providers if isinstance(p, ProxyProvider)
        )
        resources = await proxy_provider._list_resources()
        assert resources, "expected the fixture to expose at least one resource"
        for resource in resources:
            attrs = resource.get_span_attributes()
            assert attrs["fastmcp.proxy.backend_uri"] == str(resource.uri)
            assert all(v is not None for v in attrs.values()), (
                f"OpenTelemetry rejects None attribute values; got {attrs!r}"
            )

    async def test_proxy_template_span_attributes_fall_back_to_uri_template(
        self, proxy_server
    ):
        proxy_provider = next(
            p for p in proxy_server.providers if isinstance(p, ProxyProvider)
        )
        templates = await proxy_provider._list_resource_templates()
        assert templates, "expected the fixture to expose at least one template"
        for template in templates:
            attrs = template.get_span_attributes()
            assert attrs["fastmcp.proxy.backend_uri_template"] == template.uri_template
            assert all(v is not None for v in attrs.values()), (
                f"OpenTelemetry rejects None attribute values; got {attrs!r}"
            )

    async def test_proxy_prompt_span_attributes_fall_back_to_name(self, proxy_server):
        proxy_provider = next(
            p for p in proxy_server.providers if isinstance(p, ProxyProvider)
        )
        prompts = await proxy_provider._list_prompts()
        assert prompts, "expected the fixture to expose at least one prompt"
        for prompt in prompts:
            attrs = prompt.get_span_attributes()
            assert attrs["fastmcp.proxy.backend_name"] == prompt.name
            assert all(v is not None for v in attrs.values()), (
                f"OpenTelemetry rejects None attribute values; got {attrs!r}"
            )


class TestProxyOutputSchemaEnforcement:
    """A proxy relays tool results; it does not police the backend's schema.

    `ClientSession.call_tool` validates structured content against the output
    schema the backend advertised. For a proxy that check is misplaced: it
    turns a backend's schema bug into a proxy error and hides the real
    response from the client that actually consumes it.
    """

    @pytest.fixture
    def backend_violating_its_schema(self) -> FastMCP:
        mcp = FastMCP("SchemaViolator")
        schema = {
            "type": "object",
            "properties": {"status": {"enum": ["ok", "error"]}},
            "required": ["status"],
        }

        @mcp.tool(output_schema=schema)
        def undeclared_status() -> dict:
            return {"status": "weird"}

        @mcp.tool(output_schema=schema)
        def declared_status() -> dict:
            return {"status": "ok"}

        return mcp

    async def _call_without_validating(self, server: FastMCP, tool: str):
        """Call through a client that does not enforce the schema itself."""
        # This proxy's backend is built via `ProxyProvider(lambda: ProxyClient(...))`
        # directly rather than through `create_proxy`'s era-mirroring factory, so it
        # stays pinned to `ProxyClient`'s own default of `mode="legacy"` regardless
        # of the front era (see the `proxy_server` fixture docstring above for the
        # full explanation). Pin the end client to match: a modern front's request
        # `_meta` carries reserved modern-envelope keys that `ProxyTool.run`'s
        # legacy-backend path forwards verbatim, and this legacy-locked backend
        # session rejects them as a protocol violation.
        client = Client(server, mode="legacy")
        client._transport_options = TransportOptions(
            session_class=_ForwardingClientSession
        )
        async with client:
            return await client.call_tool_mcp(tool, {})

    async def test_proxy_forwards_result_violating_backend_schema(
        self, backend_violating_its_schema
    ):
        proxy = FastMCP("Proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(backend_violating_its_schema))
        )

        result = await self._call_without_validating(proxy, "undeclared_status")

        assert result.is_error is False
        assert result.structured_content == {"status": "weird"}

    async def test_proxy_forwards_conforming_result_unchanged(
        self, backend_violating_its_schema
    ):
        proxy = FastMCP("Proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(backend_violating_its_schema))
        )

        result = await self._call_without_validating(proxy, "declared_status")

        assert result.is_error is False
        assert result.structured_content == {"status": "ok"}

    async def test_end_client_still_enforces_the_schema(
        self, backend_violating_its_schema
    ):
        """Skipping validation in the proxy doesn't disarm the real client."""
        proxy = FastMCP("Proxy")
        proxy.add_provider(
            ProxyProvider(lambda: ProxyClient(backend_violating_its_schema))
        )

        # `ProxyClient(backend_violating_its_schema)` above is pinned to legacy
        # (see `_call_without_validating`'s comment); match the front here too.
        async with Client(proxy, mode="legacy") as client:
            with pytest.raises(RuntimeError, match="Invalid structured content"):
                await client.call_tool_mcp("undeclared_status", {})

    async def test_direct_client_still_enforces_the_schema(
        self, backend_violating_its_schema
    ):
        """The behavior change is scoped to proxies, not clients generally."""
        async with Client(backend_violating_its_schema) as client:
            with pytest.raises(RuntimeError, match="Invalid structured content"):
                await client.call_tool_mcp("undeclared_status", {})

    async def test_proxied_calls_do_not_refetch_the_backend_tool_list(self):
        """Validation used to force a `tools/list` on every proxied call.

        The proxy builds a fresh client per request, so the SDK's output-schema
        cache was always cold and each call paid an extra backend round trip.
        """
        counts = {"list": 0, "call": 0}

        class CountingMiddleware(Middleware):
            async def on_list_tools(self, context, call_next):
                counts["list"] += 1
                return await call_next(context)

            async def on_call_tool(self, context, call_next):
                counts["call"] += 1
                return await call_next(context)

        backend = FastMCP("Backend")
        backend.add_middleware(CountingMiddleware())

        @backend.tool(
            output_schema={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            }
        )
        def echo(n: int) -> dict:
            return {"n": n}

        proxy = FastMCP("Proxy")
        proxy.add_provider(ProxyProvider(lambda: ProxyClient(backend)))

        # `ProxyClient(backend)` above is pinned to legacy (see
        # `_call_without_validating`'s comment); match the front here too.
        async with Client(proxy, mode="legacy") as client:
            await client.call_tool("echo", {"n": 1})
            lists_after_first = counts["list"]

            for n in range(2, 5):
                await client.call_tool("echo", {"n": n})

        assert counts["call"] == 4
        assert counts["list"] == lists_after_first


class TestProxySettingsAreNotSharedBetweenClients:
    """Proxy connection settings belong to the client, not to the transport.

    Configuring a shared transport in place used to leak proxy behavior into
    unrelated clients — including header forwarding, which would send the
    caller's credentials to a server the user never meant to authorize.
    """

    def test_building_a_proxy_client_does_not_reconfigure_a_shared_transport(self):
        shared = StreamableHttpTransport("http://example.com/mcp/")
        plain = Client(shared)

        ProxyClient(shared)

        assert plain._transport_options is None

    def test_proxy_client_carries_its_own_options(self):
        proxy_client = ProxyClient(StreamableHttpTransport("http://example.com/mcp/"))

        options = proxy_client._transport_options
        assert options is not None
        assert options.forward_incoming_headers is True
        assert options.session_class is _ForwardingClientSession

    def test_options_survive_the_per_request_client_copy(self):
        """The proxy builds a fresh client per request via `new()`."""
        proxy_client = ProxyClient(StreamableHttpTransport("http://example.com/mcp/"))

        assert proxy_client.new()._transport_options is proxy_client._transport_options

    def test_a_user_supplied_client_is_not_reconfigured(self):
        """`create_proxy(client)` must not change how the caller's client behaves."""
        user_client = Client(StreamableHttpTransport("http://example.com/mcp/"))

        create_proxy(user_client)

        assert user_client._transport_options is None


class TestProxyForwardingAppliesToEveryBackendClient:
    """Every path that builds a proxy backend gets the forwarding session.

    `create_proxy` accepts plain Clients and MCPConfigs, none of which route
    through `ProxyClient.__init__`, so configuring only that constructor would
    leave those forms still rejecting backend results.
    """

    @pytest.fixture
    def backend(self) -> FastMCP:
        mcp = FastMCP("SchemaViolator")

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {"status": {"enum": ["ok", "error"]}},
                "required": ["status"],
            }
        )
        def status() -> dict:
            return {"status": "weird"}

        return mcp

    async def _forwarded(
        self, server: FastMCP, tool: str = "status", mode: str = "auto"
    ):
        # `mode` follows the proxy backend's era: a plain Client or single-server
        # config connects the backend directly, so it mirrors the front's auto
        # era. A multi-server config instead mounts a router with a
        # StatefulProxyClient per configured server leg — an already-constructed
        # ProxyClient subclass, same as the `proxy_server` fixture above, pinned
        # to `mode="legacy"` regardless of the front. Callers with that backend
        # shape must pin the end client to legacy too, for the reason explained
        # there (a modern front's request `_meta` gets forwarded verbatim onto a
        # legacy-locked backend session and rejected as a protocol violation).
        client = Client(server, mode=mode)
        client._transport_options = TransportOptions(
            session_class=_ForwardingClientSession
        )
        async with client:
            return await client.call_tool_mcp(tool, {})

    async def test_plain_client_target_forwards(self, backend):
        result = await self._forwarded(create_proxy(Client(backend)))

        assert result.is_error is False
        assert result.structured_content == {"status": "weird"}

    async def test_single_server_config_target_forwards(self, backend):
        port = find_available_port()
        async with run_server_async(backend, port=port):
            config = MCPConfig.from_dict(
                {"mcpServers": {"a": {"url": f"http://127.0.0.1:{port}/mcp/"}}}
            )
            result = await self._forwarded(create_proxy(Client(config)))

        assert result.is_error is False
        assert result.structured_content == {"status": "weird"}

    async def test_multi_server_config_target_forwards(self, backend):
        port = find_available_port()
        async with run_server_async(backend, port=port):
            url = f"http://127.0.0.1:{port}/mcp/"
            config = MCPConfig.from_dict(
                {"mcpServers": {"a": {"url": url}, "b": {"url": url}}}
            )
            result = await self._forwarded(
                create_proxy(Client(config)), "a_status", mode="legacy"
            )

        assert result.is_error is False
        assert result.structured_content == {"status": "weird"}


class TestProxyModernEraInstructions:
    """Upstream instructions must reach a client on the modern era too.

    `ProxyInitializeMiddleware.on_initialize` only fires for the legacy
    handshake. A `mode="auto"` client negotiates via `server/discover`, which
    the SDK builds from the low-level server's own `instructions`, so without a
    discover-side hook the proxy drops its upstream's instructions entirely.
    """

    async def test_proxy_forwards_upstream_instructions_on_modern_era(self):
        upstream = FastMCP(name="upstream", instructions="USE_THIS_MARKER_123")
        proxy = create_proxy(upstream, name="proxy")

        async with Client(proxy, mode="auto") as client:
            assert client.protocol_version in MODERN_PROTOCOL_VERSIONS
            assert client.session.instructions == "USE_THIS_MARKER_123"

    async def test_proxy_own_instructions_take_precedence_on_modern_era(self):
        upstream = FastMCP(name="upstream", instructions="upstream instructions")
        proxy = create_proxy(upstream, name="proxy", instructions="proxy instructions")

        async with Client(proxy, mode="auto") as client:
            assert client.session.instructions == "proxy instructions"

    async def test_proxy_instructions_none_when_upstream_has_none_on_modern_era(self):
        upstream = FastMCP(name="upstream")
        proxy = create_proxy(upstream, name="proxy")

        async with Client(proxy, mode="auto") as client:
            assert client.session.instructions is None


class TestProxyProviderTransportErrors:
    """A dead backend must surface as an MCPError, not a raw transport error.

    `ProxyTool.run` and `ProxyInitializeMiddleware.on_initialize` normalize
    connection failures into `MCPError`; the provider's list methods caught
    only `MCPError`, so an `httpx2.ConnectError` (or the `RuntimeError` the
    client wraps a failed connect in) escaped unwrapped to the caller.
    """

    @pytest.fixture
    def unreachable_provider(self) -> ProxyProvider:
        port = find_available_port()
        return ProxyProvider(
            lambda: ProxyClient(f"http://127.0.0.1:{port}/mcp/"),
            cache_ttl=0,
        )

    @pytest.mark.parametrize(
        "method",
        ["_list_tools", "_list_resources", "_list_resource_templates", "_list_prompts"],
    )
    async def test_list_method_wraps_connection_failure(
        self, unreachable_provider: ProxyProvider, method: str
    ):
        with pytest.raises(MCPError):
            await getattr(unreachable_provider, method)()

    @pytest.mark.parametrize(
        "method",
        ["_list_tools", "_list_resources", "_list_resource_templates", "_list_prompts"],
    )
    async def test_list_method_wraps_raw_transport_error(self, method: str):
        """A raw transport error raised mid-call is normalized, not leaked."""

        def exploding_factory() -> Client:
            raise httpx2.ConnectError("backend refused the connection")

        provider = ProxyProvider(exploding_factory, cache_ttl=0)
        with pytest.raises(MCPError, match="backend refused the connection"):
            await getattr(provider, method)()

    @pytest.mark.parametrize("mode", ["legacy", "auto"])
    async def test_connection_error_reaches_client_on_both_eras(self, mode: str):
        """The actual defect: the modern era masked the connection failure.

        An unwrapped `RuntimeError` reaching the modern wire boundary is
        replaced with a generic "Internal server error", so a client on the
        newer protocol could not tell a dead backend from a server bug. On the
        legacy era the same exception reached the wire as `str(exc)`, which is
        why nothing caught this while tests pinned the older version.
        """
        port = find_available_port()
        proxy = create_proxy(
            StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp"),
            provider_error_strategy="raise",
        )

        with pytest.raises(MCPError, match="Client failed to connect"):
            async with Client(proxy, mode=mode) as client:
                await client.list_tools()
