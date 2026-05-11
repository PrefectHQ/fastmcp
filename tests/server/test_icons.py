"""Tests for icon support across all MCP object types."""

from mcp.types import Icon

from fastmcp import Client, FastMCP
from fastmcp.prompts import Message, Prompt
from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools import Tool


class TestServerIcons:
    """Test icon support at the server/implementation level."""

    async def test_server_with_icons_and_website_url(self):
        """Test that server accepts icons and websiteUrl in constructor."""
        icons = [
            Icon(
                src="https://example.com/icon.png",
                mimeType="image/png",
                sizes=["48x48"],
            ),
            Icon(
                src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
                mimeType="image/svg+xml",
                sizes=["any"],
            ),
        ]

        mcp = FastMCP(
            name="TestServer",
            version="1.0.0",
            website_url="https://example.com",
            icons=icons,
        )

        # Verify that icons and website_url are passed to the underlying server
        async with Client(mcp) as client:
            server_info = client.initialize_result.serverInfo
            assert server_info.websiteUrl == "https://example.com"
            assert server_info.icons == icons

    async def test_server_without_icons_and_website_url(self):
        """Test that server works without icons and websiteUrl."""
        mcp = FastMCP(name="TestServer")

        async with Client(mcp) as client:
            server_info = client.initialize_result.serverInfo
            assert server_info.websiteUrl is None
            assert server_info.icons is None


class TestToolIcons:
    """Test icon support for tools."""

    async def test_tool_with_icons(self):
        """Test that tools can have icons."""
        mcp = FastMCP("TestServer")

        icons = [
            Icon(src="https://example.com/tool-icon.png", mimeType="image/png"),
        ]

        @mcp.tool(icons=icons)
        def my_tool(name: str) -> str:
            """A tool with an icon."""
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            tool = tools[0]
            assert tool.icons == icons

    async def test_tool_from_function_with_icons(self):
        """Test creating a tool from a function with icons."""
        icons = [Icon(src="https://example.com/icon.png")]

        def my_function(x: int) -> int:
            """A function."""
            return x * 2

        tool = Tool.from_function(my_function, icons=icons)
        assert tool.icons == icons

        # Verify it converts to MCP tool correctly
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.icons == icons

    async def test_tool_without_icons(self):
        """Test that tools work without icons."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def my_tool(name: str) -> str:
            """A tool without an icon."""
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            tool = tools[0]
            assert tool.icons is None


class TestResourceIcons:
    """Test icon support for resources."""

    async def test_resource_with_icons(self):
        """Test that resources can have icons."""
        mcp = FastMCP("TestServer")

        icons = [Icon(src="https://example.com/resource-icon.png")]

        @mcp.resource("test://resource", icons=icons)
        def my_resource() -> str:
            """A resource with an icon."""
            return "Resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1
            resource = resources[0]
            assert resource.icons == icons

    async def test_resource_from_function_with_icons(self):
        """Test creating a resource from a function with icons."""
        icons = [Icon(src="https://example.com/icon.png")]

        def my_function() -> str:
            """A function."""
            return "content"

        resource = Resource.from_function(
            my_function,
            uri="test://resource",
            icons=icons,
        )
        assert resource.icons == icons

        # Verify it converts to MCP resource correctly
        mcp_resource = resource.to_mcp_resource()
        assert mcp_resource.icons == icons

    async def test_resource_without_icons(self):
        """Test that resources work without icons."""
        mcp = FastMCP("TestServer")

        @mcp.resource("test://resource")
        def my_resource() -> str:
            """A resource without an icon."""
            return "Resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1
            resource = resources[0]
            assert resource.icons is None


class TestResourceTemplateIcons:
    """Test icon support for resource templates."""

    async def test_resource_template_with_icons(self):
        """Test that resource templates can have icons."""
        mcp = FastMCP("TestServer")

        icons = [Icon(src="https://example.com/template-icon.png")]

        @mcp.resource("test://resource/{id}", icons=icons)
        def my_template(id: str) -> str:
            """A resource template with an icon."""
            return f"Resource {id}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1
            template = templates[0]
            assert template.icons == icons

    async def test_resource_template_from_function_with_icons(self):
        """Test creating a resource template from a function with icons."""
        icons = [Icon(src="https://example.com/icon.png")]

        def my_function(id: str) -> str:
            """A function."""
            return f"content-{id}"

        template = ResourceTemplate.from_function(
            my_function,
            uri_template="test://resource/{id}",
            icons=icons,
        )
        assert template.icons == icons

        # Verify it converts to MCP template correctly
        mcp_template = template.to_mcp_template()
        assert mcp_template.icons == icons

    async def test_resource_template_without_icons(self):
        """Test that resource templates work without icons."""
        mcp = FastMCP("TestServer")

        @mcp.resource("test://resource/{id}")
        def my_template(id: str) -> str:
            """A resource template without an icon."""
            return f"Resource {id}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1
            template = templates[0]
            assert template.icons is None


class TestPromptIcons:
    """Test icon support for prompts."""

    async def test_prompt_with_icons(self):
        """Test that prompts can have icons."""
        mcp = FastMCP("TestServer")

        icons = [Icon(src="https://example.com/prompt-icon.png")]

        @mcp.prompt(icons=icons)
        def my_prompt(name: str):
            """A prompt with an icon."""
            return Message(f"Hello, {name}!")

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            prompt = prompts[0]
            assert prompt.icons == icons

    async def test_prompt_from_function_with_icons(self):
        """Test creating a prompt from a function with icons."""
        icons = [Icon(src="https://example.com/icon.png")]

        def my_function(topic: str):
            """A function."""
            return Message(f"Tell me about {topic}")

        prompt = Prompt.from_function(my_function, icons=icons)
        assert prompt.icons == icons

        # Verify it converts to MCP prompt correctly
        mcp_prompt = prompt.to_mcp_prompt()
        assert mcp_prompt.icons == icons

    async def test_prompt_without_icons(self):
        """Test that prompts work without icons."""
        mcp = FastMCP("TestServer")

        @mcp.prompt
        def my_prompt(name: str):
            """A prompt without an icon."""
            return Message(f"Hello, {name}!")

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            prompt = prompts[0]
            assert prompt.icons is None


class TestIconTypes:
    """Test different types of icon data."""

    async def test_multiple_icon_sizes(self):
        """Test that multiple icon sizes can be specified."""
        icons = [
            Icon(
                src="https://example.com/icon-48.png",
                mimeType="image/png",
                sizes=["48x48"],
            ),
            Icon(
                src="https://example.com/icon-96.png",
                mimeType="image/png",
                sizes=["96x96"],
            ),
            Icon(
                src="https://example.com/icon.svg",
                mimeType="image/svg+xml",
                sizes=["any"],
            ),
        ]

        mcp = FastMCP("TestServer", icons=icons)

        async with Client(mcp) as client:
            server_info = client.initialize_result.serverInfo
            assert len(server_info.icons) == 3
            assert server_info.icons == icons

    async def test_data_uri_icon(self):
        """Test using data URIs for icons."""
        # Simple SVG data URI
        data_uri = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCI+PHBhdGggZD0iTTEyIDJDNi40OCAyIDIgNi40OCAyIDEyczQuNDggMTAgMTAgMTAgMTAtNC40OCAxMC0xMFMxNy41MiAyIDEyIDJ6Ii8+PC9zdmc+"

        icons = [Icon(src=data_uri, mimeType="image/svg+xml")]

        mcp = FastMCP("TestServer")

        @mcp.tool(icons=icons)
        def my_tool() -> str:
            """A tool with a data URI icon."""
            return "result"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert tools[0].icons[0].src == data_uri

    async def test_icon_without_optional_fields(self):
        """Test that icons work with only the src field."""
        icons = [Icon(src="https://example.com/icon.png")]

        mcp = FastMCP("TestServer", icons=icons)

        async with Client(mcp) as client:
            server_info = client.initialize_result.serverInfo
            assert server_info.icons[0].src == "https://example.com/icon.png"
            assert server_info.icons[0].mimeType is None
            assert server_info.icons[0].sizes is None


class TestIconImport:
    """Test that Icon must be imported from mcp.types."""

    def test_icon_import(self):
        """Test that Icon must be imported from mcp.types, not fastmcp."""
        # Icon should NOT be available from fastmcp
        import fastmcp

        assert not hasattr(fastmcp, "Icon")

        # Icon should be imported from mcp.types
        from mcp.types import Icon as MCPIcon

        icon = MCPIcon(src="https://example.com/icon.png")
        assert icon.src == "https://example.com/icon.png"
