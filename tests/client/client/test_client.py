"""Core client functionality: tools, resources, prompts."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
import pytest
from mcp import ClientSession, McpError
from mcp.types import TextContent
from pydantic import AnyUrl

import fastmcp
from fastmcp.client import Client
from fastmcp.client.transports import (
    ClientTransport,
    FastMCPTransport,
)
from fastmcp.server.server import FastMCP


async def test_list_tools(fastmcp_server):
    """Test listing tools with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_tools()

        # Check that our tools are available
        assert len(result) == 3
        assert set(tool.name for tool in result) == {"greet", "add", "sleep"}


async def test_list_tools_mcp(fastmcp_server):
    """Test the list_tools_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_tools_mcp()

        # Check that we got the raw MCP ListToolsResult object
        assert hasattr(result, "tools")
        assert len(result.tools) == 3
        assert set(tool.name for tool in result.tools) == {"greet", "add", "sleep"}


async def test_call_tool(fastmcp_server):
    """Test calling a tool with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.call_tool("greet", {"name": "World"})

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Hello, World!"
        assert result.structured_content == {"result": "Hello, World!"}
        assert result.data == "Hello, World!"
        assert result.is_error is False


async def test_call_tool_mcp(fastmcp_server):
    """Test the call_tool_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.call_tool_mcp("greet", {"name": "World"})

        # Check that we got the raw MCP CallToolResult object
        assert hasattr(result, "content")
        assert hasattr(result, "isError")
        assert result.isError is False
        # The content is a list, so we'll check the first element
        # by properly accessing it
        content = result.content
        assert len(content) > 0
        first_content = content[0]
        content_str = str(first_content)
        assert "Hello, World!" in content_str


async def test_call_tool_with_meta():
    """Test that meta parameter is properly passed from client to server."""
    server = FastMCP("MetaTestServer")

    # Create a tool that accesses the meta from the request context
    @server.tool
    def check_meta() -> dict[str, Any]:
        """A tool that returns the meta from the request context."""
        from fastmcp.server.dependencies import get_context

        context = get_context()
        assert context.request_context is not None
        meta = context.request_context.meta

        # Return the meta data as a dict
        if meta is not None:
            return {
                "has_meta": True,
                "user_id": getattr(meta, "user_id", None),
                "trace_id": getattr(meta, "trace_id", None),
            }
        return {"has_meta": False}

    client = Client(transport=FastMCPTransport(server))

    async with client:
        # Test with meta parameter - verify the server receives it
        test_meta = {"user_id": "test-123", "trace_id": "abc-def"}
        result = await client.call_tool("check_meta", {}, meta=test_meta)

        assert result.data["has_meta"] is True
        assert result.data["user_id"] == "test-123"
        assert result.data["trace_id"] == "abc-def"

        # Test without meta parameter - verify fields are not present
        result_no_meta = await client.call_tool("check_meta", {})
        # When meta is not provided, custom fields should not be present
        assert result_no_meta.data.get("user_id") is None
        assert result_no_meta.data.get("trace_id") is None


async def test_list_resources(fastmcp_server):
    """Test listing resources with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_resources()

        # Check that our resource is available
        assert len(result) == 1
        assert str(result[0].uri) == "data://users"


async def test_list_resources_mcp(fastmcp_server):
    """Test the list_resources_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_resources_mcp()

        # Check that we got the raw MCP ListResourcesResult object
        assert hasattr(result, "resources")
        assert len(result.resources) == 1
        assert str(result.resources[0].uri) == "data://users"


async def test_list_prompts(fastmcp_server):
    """Test listing prompts with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_prompts()

        # Check that our prompt is available
        assert len(result) == 1
        assert result[0].name == "welcome"


async def test_list_prompts_mcp(fastmcp_server):
    """Test the list_prompts_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_prompts_mcp()

        # Check that we got the raw MCP ListPromptsResult object
        assert hasattr(result, "prompts")
        assert len(result.prompts) == 1
        assert result.prompts[0].name == "welcome"


async def test_get_prompt(fastmcp_server):
    """Test getting a prompt with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.get_prompt("welcome", {"name": "Developer"})

        # The result should contain our welcome message
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Welcome to FastMCP, Developer!"
        assert result.description == "Example greeting prompt."


async def test_get_prompt_mcp(fastmcp_server):
    """Test the get_prompt_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.get_prompt_mcp("welcome", {"name": "Developer"})

        # The result should contain our welcome message
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Welcome to FastMCP, Developer!"
        assert result.description == "Example greeting prompt."


async def test_client_serializes_all_non_string_arguments():
    """Test that client always serializes non-string arguments to JSON, regardless of server types."""
    server = FastMCP("TestServer")

    @server.prompt
    def echo_args(arg1: str, arg2: str, arg3: str) -> str:
        """Server accepts all string args but client sends mixed types."""
        return f"arg1: {arg1}, arg2: {arg2}, arg3: {arg3}"

    client = Client(transport=FastMCPTransport(server))

    async with client:
        result = await client.get_prompt(
            "echo_args",
            {
                "arg1": "hello",  # string - should pass through
                "arg2": [1, 2, 3],  # list - should be JSON serialized
                "arg3": {"key": "value"},  # dict - should be JSON serialized
            },
        )

        assert isinstance(result.messages[0].content, TextContent)
        content = result.messages[0].content.text
        assert "arg1: hello" in content
        assert "arg2: [1,2,3]" in content  # JSON serialized list
        assert 'arg3: {"key":"value"}' in content  # JSON serialized dict


async def test_client_server_type_conversion_integration():
    """Test that client serialization works with server-side type conversion."""
    server = FastMCP("TestServer")

    @server.prompt
    def typed_prompt(numbers: list[int], config: dict[str, str]) -> str:
        """Server expects typed args - will convert from JSON strings."""
        return f"Got {len(numbers)} numbers and {len(config)} config items"

    client = Client(transport=FastMCPTransport(server))

    async with client:
        result = await client.get_prompt(
            "typed_prompt",
            {"numbers": [1, 2, 3, 4], "config": {"theme": "dark", "lang": "en"}},
        )

        assert isinstance(result.messages[0].content, TextContent)
        content = result.messages[0].content.text
        assert "Got 4 numbers and 2 config items" in content


async def test_client_serialization_error():
    """Test client error when object cannot be serialized."""
    import pydantic_core

    server = FastMCP("TestServer")

    @server.prompt
    def any_prompt(data: str) -> str:
        return f"Got: {data}"

    # Create an unserializable object
    class UnserializableClass:
        def __init__(self):
            self.func = lambda x: x  # functions can't be JSON serialized

    client = Client(transport=FastMCPTransport(server))

    async with client:
        with pytest.raises(
            pydantic_core.PydanticSerializationError, match="Unable to serialize"
        ):
            await client.get_prompt("any_prompt", {"data": UnserializableClass()})


async def test_server_deserialization_error():
    """Test server error when JSON string cannot be converted to expected type."""

    server = FastMCP("TestServer")

    @server.prompt
    def strict_typed_prompt(numbers: list[int]) -> str:
        """Expects list of integers but will receive invalid JSON."""
        return f"Got {len(numbers)} numbers"

    client = Client(transport=FastMCPTransport(server))

    async with client:
        with pytest.raises(McpError, match="Error rendering prompt"):
            await client.get_prompt(
                "strict_typed_prompt",
                {
                    "numbers": "not valid json"  # This will fail server-side conversion
                },
            )


async def test_read_resource_invalid_uri(fastmcp_server):
    """Test reading a resource with an invalid URI."""
    client = Client(transport=FastMCPTransport(fastmcp_server))
    with pytest.raises(ValueError, match="Provided resource URI is invalid"):
        await client.read_resource("invalid_uri")


async def test_read_resource(fastmcp_server):
    """Test reading a resource with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        # Use the URI from the resource we know exists in our server
        uri = cast(
            AnyUrl, "data://users"
        )  # Use cast for type hint only, the URI is valid
        result = await client.read_resource(uri)

        # The contents should include our user list
        contents_str = str(result[0])
        assert "Alice" in contents_str
        assert "Bob" in contents_str
        assert "Charlie" in contents_str


async def test_read_resource_mcp(fastmcp_server):
    """Test the read_resource_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        # Use the URI from the resource we know exists in our server
        uri = cast(
            AnyUrl, "data://users"
        )  # Use cast for type hint only, the URI is valid
        result = await client.read_resource_mcp(uri)

        # Check that we got the raw MCP ReadResourceResult object
        assert hasattr(result, "contents")
        assert len(result.contents) > 0
        contents_str = str(result.contents[0])
        assert "Alice" in contents_str
        assert "Bob" in contents_str
        assert "Charlie" in contents_str


async def test_client_connection(fastmcp_server):
    """Test that connect is idempotent."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Connect idempotently
    async with client:
        assert client.is_connected()
        # Make a request to ensure connection is working
        await client.ping()
    assert not client.is_connected()


async def test_initialize_called_once(fastmcp_server):
    """Test that initialization is called once and sets initialize_result."""
    client = Client(transport=FastMCPTransport(fastmcp_server))
    async with client:
        # Verify that initialization succeeded by checking initialize_result
        assert client.initialize_result is not None
        assert client.initialize_result.serverInfo is not None


async def test_initialize_result_connected(fastmcp_server):
    """Test that initialize_result returns the correct result when connected."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Initialize result should be None before connection
    assert client.initialize_result is None

    async with client:
        # Once connected, initialize_result should be available
        result = client.initialize_result

        # Verify the initialize result has expected properties
        assert hasattr(result, "serverInfo")
        assert result.serverInfo.name == "TestServer"
        assert result.serverInfo.version is not None


async def test_initialize_result_disconnected(fastmcp_server):
    """Test that initialize_result is None when not connected."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Initialize result should be None before connection
    assert client.initialize_result is None

    # Connect and then disconnect
    async with client:
        assert client.is_connected()

    # After disconnection, initialize_result should be None again
    assert not client.is_connected()
    assert client.initialize_result is None


async def test_server_info_custom_version():
    """Test that custom version is properly set in serverInfo."""
    # Test with custom version
    server_with_version = FastMCP("CustomVersionServer", version="1.2.3")
    client = Client(transport=FastMCPTransport(server_with_version))

    async with client:
        result = client.initialize_result
        assert result is not None
        assert result.serverInfo.name == "CustomVersionServer"
        assert result.serverInfo.version == "1.2.3"

    # Test without version (backward compatibility)
    server_without_version = FastMCP("DefaultVersionServer")
    client = Client(transport=FastMCPTransport(server_without_version))

    async with client:
        result = client.initialize_result
        assert result is not None
        assert result.serverInfo.name == "DefaultVersionServer"
        # Should fall back to FastMCP version
        assert result.serverInfo.version == fastmcp.__version__


class _DelayedConnectTransport(ClientTransport):
    def __init__(
        self,
        inner: ClientTransport,
        connect_started: anyio.Event,
        allow_connect: anyio.Event,
    ) -> None:
        self._inner = inner
        self._connect_started = connect_started
        self._allow_connect = allow_connect

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Any
    ) -> AsyncIterator[ClientSession]:
        self._connect_started.set()
        await self._allow_connect.wait()
        async with self._inner.connect_session(**session_kwargs) as session:
            yield session

    async def close(self) -> None:
        await self._inner.close()


async def test_client_nested_context_manager(fastmcp_server):
    """Test that the client connects and disconnects once in nested context manager."""

    client = Client(fastmcp_server)

    # Before connection
    assert not client.is_connected()
    assert client._session_state.session is None

    # During connection
    async with client:
        assert client.is_connected()
        assert client._session_state.session is not None
        session = client._session_state.session

        # Reuse the same session
        async with client:
            assert client.is_connected()
            assert client._session_state.session is session

        # Reuse the same session
        async with client:
            assert client.is_connected()
            assert client._session_state.session is session

    # After connection
    assert not client.is_connected()
    assert client._session_state.session is None


async def test_client_context_entry_cancelled_starter_cleans_up(fastmcp_server):
    connect_started = anyio.Event()
    allow_connect = anyio.Event()

    client = Client(
        transport=_DelayedConnectTransport(
            FastMCPTransport(fastmcp_server),
            connect_started=connect_started,
            allow_connect=allow_connect,
        )
    )

    async def enter_and_never_reach_body() -> None:
        async with client:
            pytest.fail(
                "Context body should not be reached when __aenter__ is cancelled"
            )

    task = asyncio.create_task(enter_and_never_reach_body())
    await connect_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Connection startup was cancelled; session state should be fully reset.
    assert client._session_state.session_task is None
    assert client._session_state.session is None
    assert client._session_state.nesting_counter == 0

    # A future connection attempt should work normally.
    allow_connect.set()
    async with client:
        tools = await client.list_tools()
        assert len(tools) == 3


async def test_cancelled_context_entry_waiter_does_not_close_active_session(
    fastmcp_server,
):
    connect_started = anyio.Event()
    allow_connect = anyio.Event()

    client = Client(
        transport=_DelayedConnectTransport(
            FastMCPTransport(fastmcp_server),
            connect_started=connect_started,
            allow_connect=allow_connect,
        )
    )

    b_done = asyncio.Event()
    b_started = asyncio.Event()

    async def task_a() -> int:
        async with client:
            await b_done.wait()
            tools = await client.list_tools()
            return len(tools)

    async def task_b() -> None:
        b_started.set()
        async with client:
            pytest.fail("This context should never be entered due to cancellation")

    a = asyncio.create_task(task_a())
    await connect_started.wait()

    b = asyncio.create_task(task_b())
    await b_started.wait()
    await asyncio.sleep(0)  # let task_b attempt to acquire the client lock

    b.cancel()
    allow_connect.set()

    with pytest.raises(asyncio.CancelledError):
        await b

    # task_b is fully cancelled; allow task_a to exercise the connected session.
    b_done.set()
    assert await a == 3


async def test_concurrent_client_context_managers():
    """
    Test that concurrent client usage doesn't cause cross-task cancel scope issues.
    https://github.com/jlowin/fastmcp/pull/643
    """
    # Create a simple server
    server = FastMCP("Test Server")

    @server.tool
    def echo(text: str) -> str:
        """Echo tool"""
        return text

    # Create client
    client = Client(server)

    # Track results
    results = {}
    errors = []

    async def use_client(task_id: str, delay: float = 0):
        """Use the client with a small delay to ensure overlap"""
        try:
            async with client:
                # Add a small delay to ensure contexts overlap
                await asyncio.sleep(delay)
                # Make an actual call to exercise the session
                tools = await client.list_tools()
                results[task_id] = len(tools)
        except Exception as e:
            errors.append((task_id, str(e)))

    # Run multiple tasks concurrently
    # The key is having them enter and exit the context at different times
    await asyncio.gather(
        use_client("task1", 0.0),
        use_client("task2", 0.01),  # Slight delay to ensure overlap
        use_client("task3", 0.02),
        return_exceptions=False,
    )

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 3
    assert all(count == 1 for count in results.values())  # All should see 1 tool


async def test_resource_template(fastmcp_server):
    """Test using a resource template with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        # First, list templates
        result = await client.list_resource_templates()

        # Check that our template is available
        assert len(result) == 1
        assert "data://user/{user_id}" in result[0].uriTemplate

        # Now use the template with a specific user_id
        uri = cast(AnyUrl, "data://user/123")
        result = await client.read_resource(uri)

        # Check the content matches what we expect for the provided user_id
        content_str = str(result[0])
        assert '"id":"123"' in content_str
        assert '"name":"User 123"' in content_str
        assert '"active":true' in content_str


async def test_list_resource_templates_mcp(fastmcp_server):
    """Test the list_resource_templates_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_resource_templates_mcp()

        # Check that we got the raw MCP ListResourceTemplatesResult object
        assert hasattr(result, "resourceTemplates")
        assert len(result.resourceTemplates) == 1
        assert "data://user/{user_id}" in result.resourceTemplates[0].uriTemplate


async def test_mcp_resource_generation(fastmcp_server):
    """Test that resources are properly generated in MCP format."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        resources = await client.list_resources()
        assert len(resources) == 1
        resource = resources[0]

        # Verify resource has correct MCP format
        assert hasattr(resource, "uri")
        assert hasattr(resource, "name")
        assert hasattr(resource, "description")
        assert str(resource.uri) == "data://users"


async def test_mcp_template_generation(fastmcp_server):
    """Test that templates are properly generated in MCP format."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        templates = await client.list_resource_templates()
        assert len(templates) == 1
        template = templates[0]

        # Verify template has correct MCP format
        assert hasattr(template, "uriTemplate")
        assert hasattr(template, "name")
        assert hasattr(template, "description")
        assert "data://user/{user_id}" in template.uriTemplate


async def test_template_access_via_client(fastmcp_server):
    """Test that templates can be accessed through a client."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        # Verify template works correctly when accessed
        uri = cast(AnyUrl, "data://user/456")
        result = await client.read_resource(uri)
        content_str = str(result[0])
        assert '"id":"456"' in content_str


async def test_tagged_resource_metadata(tagged_resources_server):
    """Test that resource metadata is preserved in MCP format."""
    client = Client(transport=FastMCPTransport(tagged_resources_server))

    async with client:
        resources = await client.list_resources()
        assert len(resources) == 1
        resource = resources[0]

        # Verify resource metadata is preserved
        assert str(resource.uri) == "data://tagged"
        assert resource.description == "A tagged resource"


async def test_tagged_template_metadata(tagged_resources_server):
    """Test that template metadata is preserved in MCP format."""
    client = Client(transport=FastMCPTransport(tagged_resources_server))

    async with client:
        templates = await client.list_resource_templates()
        assert len(templates) == 1
        template = templates[0]

        # Verify template metadata is preserved
        assert "template://{id}" in template.uriTemplate
        assert template.description == "A tagged template"


async def test_tagged_template_functionality(tagged_resources_server):
    """Test that tagged templates function correctly when accessed."""
    client = Client(transport=FastMCPTransport(tagged_resources_server))

    async with client:
        # Verify template functionality
        uri = cast(AnyUrl, "template://123")
        result = await client.read_resource(uri)
        content_str = str(result[0])
        assert '"id":"123"' in content_str
        assert '"type":"template_data"' in content_str
