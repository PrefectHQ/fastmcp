import asyncio
from typing import cast

import pytest
from mcp import McpError
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.prompts.prompt import TextContent
from fastmcp.server.server import FastMCP


@pytest.fixture
def fastmcp_server():
    """Fixture that creates a FastMCP server with tools, resources, and prompts."""
    server = FastMCP("TestServer")

    # Add a tool
    @server.tool()
    def greet(name: str) -> str:
        """Greet someone by name."""
        return f"Hello, {name}!"

    # Add a second tool
    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @server.tool()
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
    @server.prompt()
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
    """Test that the client connects and disconnects properly."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    # Before connection
    assert not client.is_connected()

    # During connection
    async with client:
        assert client.is_connected()

    # After connection
    assert not client.is_connected()


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
    async def test_general_tool_exceptions_are_masked(self):
        mcp = FastMCP("TestServer")

        @mcp.tool()
        def error_tool():
            raise ValueError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("error_tool", {})
            assert result.isError
            assert isinstance(result.content[0], TextContent)
            assert "test error" not in result.content[0].text
            assert "abc" not in result.content[0].text

    async def test_specific_tool_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.tool()
        def custom_error_tool():
            raise ToolError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("custom_error_tool", {})
            assert result.isError
            assert isinstance(result.content[0], TextContent)
            assert "test error" in result.content[0].text
            assert "abc" in result.content[0].text

    async def test_general_resource_exceptions_are_masked(self):
        mcp = FastMCP("TestServer")

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

    async def test_general_template_exceptions_are_masked(self):
        mcp = FastMCP("TestServer")

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


class TestTimeout:
    async def test_timeout(self, fastmcp_server: FastMCP):
        async with Client(
            transport=FastMCPTransport(fastmcp_server), timeout=0.01
        ) as client:
            with pytest.raises(
                McpError,
                match="Timed out while waiting for response to ClientRequest. Waited 0.01 seconds",
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

    async def test_timeout_tool_call_overrides_client_timeout_even_if_lower(
        self, fastmcp_server: FastMCP
    ):
        async with Client(
            transport=FastMCPTransport(fastmcp_server),
            timeout=0.01,
        ) as client:
            await client.call_tool("sleep", {"seconds": 0.1}, timeout=2)
