from typing import cast

import pytest
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
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


@pytest.fixture
def mounted_resources_server():
    """Fixture that creates a FastMCP server with mounted resources."""
    # Create the main server
    main_server = FastMCP("MainServer")

    # Create sub-app with its own resources
    sub_app = FastMCP("SubAppServer")

    # Add a resource to the sub-app
    @sub_app.resource(uri="subapp://data", description="SubApp resource")
    async def get_subapp_data():
        return {"source": "subapp"}

    # Add a template to the sub-app
    @sub_app.resource(uri="subapp://{id}", description="SubApp template")
    async def get_subapp_item(id: str):
        return {"id": id, "source": "subapp"}

    # Mount the sub-app to the main server with a prefix
    main_server.mount("sub", sub_app)

    # Add a resource to the main server
    @main_server.resource(uri="main://data", description="Main resource")
    async def get_main_data():
        return {"source": "main"}

    return main_server


async def test_list_tools(fastmcp_server):
    """Test listing tools with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_tools()

        # Check that our tools are available
        assert len(result) == 2
        assert set(tool.name for tool in result) == {"greet", "add"}


async def test_call_tool(fastmcp_server):
    """Test calling a tool with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.call_tool("greet", {"name": "World"})

        # The result content should contain our greeting
        content_str = str(result[0])
        assert "Hello, World!" in content_str


async def test_list_resources(fastmcp_server):
    """Test listing resources with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_resources()

        # Check that our resource is available
        assert len(result) == 1
        assert str(result[0].uri) == "data://users"


async def test_list_prompts(fastmcp_server):
    """Test listing prompts with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.list_prompts()

        # Check that our prompt is available
        assert len(result) == 1
        assert result[0].name == "welcome"


async def test_get_prompt(fastmcp_server):
    """Test getting a prompt with InMemoryClient."""
    client = Client(transport=FastMCPTransport(fastmcp_server))

    async with client:
        result = await client.get_prompt("welcome", {"name": "Developer"})

        # The result should contain our welcome message
        result_str = str(result)
        assert "Welcome to FastMCP, Developer!" in result_str


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


async def test_mounted_resources(mounted_resources_server):
    """Test that resources from mounted apps are correctly prefixed."""
    client = Client(transport=FastMCPTransport(mounted_resources_server))

    async with client:
        resources = await client.list_resources()

        # Should have two resources (one from main, one from sub)
        assert len(resources) == 2

        # Find resources by URI
        main_resource = next(
            (r for r in resources if str(r.uri) == "main://data"), None
        )
        sub_resource = next(
            (r for r in resources if str(r.uri) == "sub+subapp://data"), None
        )

        # Both resources should exist
        assert main_resource is not None
        assert sub_resource is not None

        # Check descriptions
        assert main_resource.description == "Main resource"
        assert sub_resource.description == "SubApp resource"


async def test_mounted_templates(mounted_resources_server):
    """Test that templates from mounted apps are correctly prefixed."""
    client = Client(transport=FastMCPTransport(mounted_resources_server))

    async with client:
        templates = await client.list_resource_templates()

        # Should have one template (from sub)
        assert len(templates) == 1

        # Check the template
        template = templates[0]
        assert "sub+subapp://{id}" in template.uriTemplate
        assert template.description == "SubApp template"


async def test_mounted_template_functionality(mounted_resources_server):
    """Test that templates from mounted apps function correctly."""
    client = Client(transport=FastMCPTransport(mounted_resources_server))

    async with client:
        # Use the prefixed template
        uri = cast(AnyUrl, "sub+subapp://123")
        result = await client.read_resource(uri)
        content_str = str(result[0])

        # Check the content
        assert '"id": "123"' in content_str
        assert '"source": "subapp"' in content_str
