import asyncio
import sys
from typing import cast
from unittest.mock import AsyncMock

import mcp
import pytest
from mcp import McpError
from mcp.client.auth import OAuthClientProvider
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.transports import (
    FastMCPTransport,
    MCPConfigTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
    infer_transport,
)
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.server import FastMCP


@pytest.fixture
def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")

    # Add a tool
    @server.tool
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    # Add a second tool
    @server.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool
    async def sleep(seconds: float) -> str:
        """Sleep for a given number of seconds."""
        await asyncio.sleep(seconds)
        return f"Slept for {seconds} seconds"

    # Add a resource
    @server.resource(uri="data://users")
    async def get_users():
        return ["Alice", "Bob", "Charlie"]

    # Add a resource template
    @server.resource(uri="data://user/{user_id}")
    async def get_user(user_id: str):
        return {"id": user_id, "name": f"User {user_id}", "active": True}

    # Add a prompt
    @server.prompt
    def welcome(name: str) -> str:
        """Example greeting prompt."""
        return f"Welcome to FastMCP, {name}!"

    return server


@pytest.fixture
def tagged_resources_server():
    """Fixture that creates a FastMCP server with tagged resources and templates."""
    server = FastMCP("TaggedResourcesServer")

    # Add a resource with tags
    @server.resource(
        uri="data://tagged", tags={"test", "metadata"}, description="A tagged resource"
    )
    async def get_tagged_data():
        return {"type": "tagged_data"}

    # Add a resource template with tags
    @server.resource(
        uri="template://{id}",
        tags={"template", "parameterized"},
        description="A tagged template",
    )
    async def get_template_data(id: str):
        return {"id": id, "type": "template_data"}

    return server


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

        # The result content should contain our greeting
        content_str = str(result[0])
        assert "Hello, World!" in content_str


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
        assert result.messages[0].content.text == "Welcome to FastMCP, Developer!"  # type: ignore[attr-defined]
        assert result.description == "Example greeting prompt."


async def test_get_prompt_mcp(fastmcp_server):
    """Test the get_prompt_mcp method that returns raw MCP protocol objects."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.get_prompt_mcp("welcome", {"name": "Developer"})

        # The result should contain our welcome message
        assert result.messages[0].content.text == "Welcome to FastMCP, Developer!"  # type: ignore[attr-defined]
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

        content = result.messages[0].content.text  # type: ignore[attr-defined]
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

        content = result.messages[0].content.text  # type: ignore[attr-defined]
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
    from mcp import McpError

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


async def test_initialize_called_once(fastmcp_server, monkeypatch):
    mock_initialize = AsyncMock()
    monkeypatch.setattr(mcp.ClientSession, "initialize", mock_initialize)
    client = Client(transport=FastMCPTransport(fastmcp_server))
    async with client:
        assert mock_initialize.call_count == 1


async def test_initialize_result_connected(fastmcp_server):
    """Test that initialize_result returns the correct result when connected."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Initialize result should not be accessible before connection
    with pytest.raises(RuntimeError, match="Client is not connected"):
        _ = client.initialize_result

    async with client:
        # Once connected, initialize_result should be available
        result = client.initialize_result

        # Verify the initialize result has expected properties
        assert hasattr(result, "serverInfo")
        assert result.serverInfo.name == "TestServer"
        assert result.serverInfo.version is not None


async def test_initialize_result_disconnected(fastmcp_server):
    """Test that initialize_result raises an error when not connected."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Initialize result should not be accessible before connection
    with pytest.raises(RuntimeError, match="Client is not connected"):
        _ = client.initialize_result

    # Connect and then disconnect
    async with client:
        assert client.is_connected()

    # After disconnection, initialize_result should raise an error
    assert not client.is_connected()
    with pytest.raises(RuntimeError, match="Client is not connected"):
        _ = client.initialize_result


async def test_server_info_custom_version():
    """Test that custom version is properly set in serverInfo."""
    # Test with custom version
    server_with_version = FastMCP("CustomVersionServer", version="1.2.3")
    client = Client(transport=FastMCPTransport(server_with_version))

    async with client:
        result = client.initialize_result
        assert result.serverInfo.name == "CustomVersionServer"
        assert result.serverInfo.version == "1.2.3"

    # Test without version (backward compatibility)
    server_without_version = FastMCP("DefaultVersionServer")
    client = Client(transport=FastMCPTransport(server_without_version))

    async with client:
        result = client.initialize_result
        assert result.serverInfo.name == "DefaultVersionServer"
        # Should fall back to MCP library version
        assert result.serverInfo.version is not None
        assert (
            result.serverInfo.version != "1.2.3"
        )  # Should be different from custom version


async def test_client_nested_context_manager(fastmcp_server):
    """Test that the client connects and disconnects once in nested context manager."""

    client = Client(fastmcp_server)

    # Before connection
    assert not client.is_connected()
    assert client._session is None

    # During connection
    async with client:
        assert client.is_connected()
        assert client._session is not None
        session = client._session

        # Re-use the same session
        async with client:
            assert client.is_connected()
            assert client._session is session

        # Re-use the same session
        async with client:
            assert client.is_connected()
            assert client._session is session

    # After connection
    assert not client.is_connected()
    assert client._session is None


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
        assert '"id": "123"' in content_str
        assert '"name": "User 123"' in content_str
        assert '"active": true' in content_str


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
        assert '"id": "456"' in content_str


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
        assert '"id": "123"' in content_str
        assert '"type": "template_data"' in content_str


class TestErrorHandling:
    async def test_general_tool_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def error_tool():
            raise ValueError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("error_tool", {})
            assert result.isError
            assert "test error" in result.content[0].text  # type: ignore[attr-defined]
            assert "abc" in result.content[0].text  # type: ignore[attr-defined]

    async def test_general_tool_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.tool
        def error_tool():
            raise ValueError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("error_tool", {})
            assert result.isError
            assert "test error" not in result.content[0].text  # type: ignore[attr-defined]
            assert "abc" not in result.content[0].text  # type: ignore[attr-defined]

    async def test_specific_tool_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def custom_error_tool():
            raise ToolError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("custom_error_tool", {})
            assert result.isError
            assert "test error" in result.content[0].text  # type: ignore[attr-defined]
            assert "abc" in result.content[0].text  # type: ignore[attr-defined]

    async def test_general_resource_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="exception://resource")
        async def exception_resource():
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" in str(excinfo.value)
            assert "internal error" in str(excinfo.value)

    async def test_general_resource_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.resource(uri="exception://resource")
        async def exception_resource():
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" not in str(excinfo.value)
            assert "internal error" not in str(excinfo.value)

    async def test_resource_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="error://resource")
        async def error_resource():
            raise ResourceError("This is a resource error (xyz)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("error://resource"))
            assert "This is a resource error (xyz)" in str(excinfo.value)

    async def test_general_template_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="exception://resource/{id}")
        async def exception_resource(id: str):
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource/123"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" in str(excinfo.value)
            assert "internal error" in str(excinfo.value)

    async def test_general_template_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.resource(uri="exception://resource/{id}")
        async def exception_resource(id: str):
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource/123"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" not in str(excinfo.value)
            assert "internal error" not in str(excinfo.value)

    async def test_template_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="error://resource/{id}")
        async def error_resource(id: str):
            raise ResourceError("This is a resource error (xyz)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("error://resource/123"))
            assert "This is a resource error (xyz)" in str(excinfo.value)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Timeout tests are flaky on Windows. Timeouts *are* supported but the tests are unreliable.",
)
class TestTimeout:
    async def test_timeout(self, fastmcp_server: FastMCP):
        async with Client(
            transport=FastMCPTransport(fastmcp_server), timeout=0.05
        ) as client:
            with pytest.raises(
                McpError,
                match="Timed out while waiting for response to ClientRequest. Waited 0.05 seconds",
            ):
                await client.call_tool("sleep", {"seconds": 0.1})

    async def test_timeout_tool_call(self, fastmcp_server: FastMCP):
        async with Client(transport=FastMCPTransport(fastmcp_server)) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    async def test_timeout_tool_call_overrides_client_timeout(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=2,
        ) as client:
            with pytest.raises(McpError):
                await client.call_tool("sleep", {"seconds": 0.1}, timeout=0.01)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="This test is flaky on Windows. Sometimes the client timeout is respected and sometimes it is not.",
    )
    async def test_timeout_tool_call_overrides_client_timeout_even_if_lower(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=0.01,
        ) as client:
            await client.call_tool("sleep", {"seconds": 0.1}, timeout=2)


class TestInferTransport:
    """Tests for the infer_transport function."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/api/sse/stream",
            "https://localhost:8080/mcp/sse/endpoint",
            "http://example.com/api/sse",
            "http://example.com/api/sse/",
            "https://localhost:8080/mcp/sse/",
            "http://example.com/api/sse?param=value",
            "https://localhost:8080/mcp/sse/?param=value",
            "https://localhost:8000/mcp/sse?x=1&y=2",
        ],
        ids=[
            "path_with_sse_directory",
            "path_with_sse_subdirectory",
            "path_ending_with_sse",
            "path_ending_with_sse_slash",
            "path_ending_with_sse_https",
            "path_with_sse_and_query_params",
            "path_with_sse_slash_and_query_params",
            "path_with_sse_and_ampersand_param",
        ],
    )
    def test_url_returns_sse_transport(self, url):
        """Test that URLs with /sse/ pattern return SSETransport."""
        assert isinstance(infer_transport(url), SSETransport)

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/api",
            "https://localhost:8080/mcp/",
            "http://example.com/asset/image.jpg",
            "https://localhost:8080/sservice/endpoint",
            "https://example.com/assets/file",
        ],
        ids=[
            "regular_http_url",
            "regular_https_url",
            "url_with_unrelated_path",
            "url_with_sservice_in_path",
            "url_with_assets_in_path",
        ],
    )
    def test_url_returns_streamable_http_transport(self, url):
        """Test that URLs without /sse/ pattern return StreamableHttpTransport."""
        assert isinstance(infer_transport(url), StreamableHttpTransport)

    def test_infer_remote_transport_from_config(self):
        config = {
            "mcpServers": {
                "test_server": {
                    "url": "http://localhost:8000/sse/",
                    "headers": {"Authorization": "Bearer 123"},
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        assert isinstance(transport.transport, SSETransport)
        assert transport.transport.url == "http://localhost:8000/sse/"
        assert transport.transport.headers == {"Authorization": "Bearer 123"}

    def test_infer_local_transport_from_config(self):
        config = {
            "mcpServers": {
                "test_server": {
                    "command": "echo",
                    "args": ["hello"],
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        assert isinstance(transport.transport, StdioTransport)
        assert transport.transport.command == "echo"
        assert transport.transport.args == ["hello"]

    def test_config_with_no_servers(self):
        """Test that an empty MCPConfig raises a ValueError."""
        config = {"mcpServers": {}}
        with pytest.raises(ValueError, match="No MCP servers defined in the config"):
            infer_transport(config)

    def test_mcpconfigtransport_with_no_servers(self):
        """Test that MCPConfigTransport raises a ValueError when initialized with an empty config."""
        config = {"mcpServers": {}}
        with pytest.raises(ValueError, match="No MCP servers defined in the config"):
            MCPConfigTransport(config=config)

    def test_infer_composite_client(self):
        config = {
            "mcpServers": {
                "local": {
                    "command": "echo",
                    "args": ["hello"],
                },
                "remote": {
                    "url": "http://localhost:8000/sse/",
                    "headers": {"Authorization": "Bearer 123"},
                },
            }
        }
        transport = infer_transport(config)
        assert isinstance(transport, MCPConfigTransport)
        assert isinstance(transport.transport, FastMCPTransport)
        assert (
            len(
                cast(FastMCP, transport.transport.server)._tool_manager._mounted_servers
            )
            == 2
        )

    def test_infer_fastmcp_server(self, fastmcp_server):
        """FastMCP server instances should infer to FastMCPTransport."""
        transport = infer_transport(fastmcp_server)
        assert isinstance(transport, FastMCPTransport)

    def test_infer_fastmcp_v1_server(self):
        """FastMCP 1.0 server instances should infer to FastMCPTransport."""
        from mcp.server.fastmcp import FastMCP as FastMCP1

        server = FastMCP1()
        transport = infer_transport(server)
        assert isinstance(transport, FastMCPTransport)


class TestAuth:
    def test_default_auth_is_none(self):
        client = Client(transport=StreamableHttpTransport("http://localhost:8000"))
        assert client.transport.auth is None

    def test_stdio_doesnt_support_auth(self):
        with pytest.raises(ValueError, match="This transport does not support auth"):
            Client(transport=StdioTransport("echo", ["hello"]), auth="oauth")

    def test_oauth_literal_sets_up_oauth_shttp(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000"), auth="oauth"
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_pass_direct_to_transport(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000", auth="oauth"),
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_sets_up_oauth_sse(self):
        client = Client(transport=SSETransport("http://localhost:8000"), auth="oauth")
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_oauth_literal_pass_direct_to_transport_sse(self):
        client = Client(transport=SSETransport("http://localhost:8000", auth="oauth"))
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, OAuthClientProvider)

    def test_auth_string_sets_up_bearer_auth_shttp(self):
        client = Client(
            transport=StreamableHttpTransport("http://localhost:8000"),
            auth="test_token",
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_pass_direct_to_transport_shttp(self):
        client = Client(
            transport=StreamableHttpTransport(
                "http://localhost:8000", auth="test_token"
            ),
        )
        assert isinstance(client.transport, StreamableHttpTransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_sets_up_bearer_auth_sse(self):
        client = Client(
            transport=SSETransport("http://localhost:8000"),
            auth="test_token",
        )
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"

    def test_auth_string_pass_direct_to_transport_sse(self):
        client = Client(
            transport=SSETransport("http://localhost:8000", auth="test_token"),
        )
        assert isinstance(client.transport, SSETransport)
        assert isinstance(client.transport.auth, BearerAuth)
        assert client.transport.auth.token.get_secret_value() == "test_token"
