"""Tests for the inspect.py module."""

import importlib.metadata

from mcp.server.fastmcp import FastMCP as FastMCP1x

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp.utilities.inspect import (
    FastMCPInfo,
    InspectFormat,
    ToolInfo,
    format_fastmcp_info,
    format_info,
    format_mcp_info,
    inspect_fastmcp,
    inspect_fastmcp_v1,
)


class TestFastMCPInfo:
    """Tests for the FastMCPInfo dataclass."""

    def test_fastmcp_info_creation(self):
        """Test that FastMCPInfo can be created with all required fields."""
        tool = ToolInfo(
            key="tool1",
            name="tool1",
            description="Test tool",
            input_schema={},
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        )
        info = FastMCPInfo(
            name="TestServer",
            instructions="Test instructions",
            fastmcp_version="1.0.0",
            mcp_version="1.0.0",
            server_generation=2,
            version="1.0.0",
            website_url=None,
            icons=None,
            tools=[tool],
            prompts=[],
            resources=[],
            templates=[],
            capabilities={"tools": {"listChanged": True}},
        )

        assert info.name == "TestServer"
        assert info.instructions == "Test instructions"
        assert info.fastmcp_version == "1.0.0"
        assert info.mcp_version == "1.0.0"
        assert info.server_generation == 2
        assert info.version == "1.0.0"
        assert len(info.tools) == 1
        assert info.tools[0].name == "tool1"
        assert info.capabilities == {"tools": {"listChanged": True}}

    def test_fastmcp_info_with_none_instructions(self):
        """Test that FastMCPInfo works with None instructions."""
        info = FastMCPInfo(
            name="TestServer",
            instructions=None,
            fastmcp_version="1.0.0",
            mcp_version="1.0.0",
            server_generation=2,
            version="1.0.0",
            website_url=None,
            icons=None,
            tools=[],
            prompts=[],
            resources=[],
            templates=[],
            capabilities={},
        )

        assert info.instructions is None


class TestGetFastMCPInfo:
    """Tests for the get_fastmcp_info function."""

    async def test_empty_server(self):
        """Test get_fastmcp_info with an empty server."""
        mcp = FastMCP("EmptyServer")

        info = await inspect_fastmcp(mcp)

        assert info.name == "EmptyServer"
        assert info.instructions is None
        assert info.fastmcp_version == fastmcp.__version__
        assert info.mcp_version == importlib.metadata.version("mcp")
        assert info.server_generation == 2  # v2 server
        assert info.version == fastmcp.__version__
        assert info.tools == []
        assert info.prompts == []
        assert info.resources == []
        assert info.templates == []
        assert "tools" in info.capabilities
        assert "resources" in info.capabilities
        assert "prompts" in info.capabilities
        assert "logging" in info.capabilities

    async def test_server_with_instructions(self):
        """Test get_fastmcp_info with a server that has instructions."""
        mcp = FastMCP("InstructionsServer", instructions="Test instructions")
        info = await inspect_fastmcp(mcp)
        assert info.instructions == "Test instructions"

    async def test_server_with_version(self):
        """Test get_fastmcp_info with a server that has a version."""
        mcp = FastMCP("VersionServer", version="1.2.3")
        info = await inspect_fastmcp(mcp)
        assert info.version == "1.2.3"

    async def test_server_with_tools(self):
        """Test get_fastmcp_info with a server that has tools."""
        mcp = FastMCP("ToolServer")

        @mcp.tool
        def add_numbers(a: int, b: int) -> int:
            return a + b

        @mcp.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        info = await inspect_fastmcp(mcp)

        assert info.name == "ToolServer"
        assert len(info.tools) == 2
        tool_names = [tool.name for tool in info.tools]
        assert "add_numbers" in tool_names
        assert "greet" in tool_names

    async def test_server_with_resources(self):
        """Test get_fastmcp_info with a server that has resources."""
        mcp = FastMCP("ResourceServer")

        @mcp.resource("resource://static")
        def get_static_data() -> str:
            return "Static data"

        @mcp.resource("resource://dynamic/{param}")
        def get_dynamic_data(param: str) -> str:
            return f"Dynamic data: {param}"

        info = await inspect_fastmcp(mcp)

        assert info.name == "ResourceServer"
        assert len(info.resources) == 1  # Static resource
        assert len(info.templates) == 1  # Dynamic resource becomes template
        resource_uris = [res.uri for res in info.resources]
        template_uris = [tmpl.uri_template for tmpl in info.templates]
        assert "resource://static" in resource_uris
        assert "resource://dynamic/{param}" in template_uris

    async def test_server_with_prompts(self):
        """Test get_fastmcp_info with a server that has prompts."""
        mcp = FastMCP("PromptServer")

        @mcp.prompt
        def analyze_data(data: str) -> list:
            return [{"role": "user", "content": f"Analyze: {data}"}]

        @mcp.prompt("custom_prompt")
        def custom_analysis(text: str) -> list:
            return [{"role": "user", "content": f"Custom: {text}"}]

        info = await inspect_fastmcp(mcp)

        assert info.name == "PromptServer"
        assert len(info.prompts) == 2
        prompt_names = [prompt.name for prompt in info.prompts]
        assert "analyze_data" in prompt_names
        assert "custom_prompt" in prompt_names

    async def test_comprehensive_server(self):
        """Test get_fastmcp_info with a server that has all component types."""
        mcp = FastMCP("ComprehensiveServer", instructions="A server with everything")

        # Add a tool
        @mcp.tool
        def calculate(x: int, y: int) -> int:
            return x * y

        # Add a resource
        @mcp.resource("resource://data")
        def get_data() -> str:
            return "Some data"

        # Add a template
        @mcp.resource("resource://item/{id}")
        def get_item(id: str) -> str:
            return f"Item {id}"

        # Add a prompt
        @mcp.prompt
        def analyze(content: str) -> list:
            return [{"role": "user", "content": content}]

        info = await inspect_fastmcp(mcp)

        assert info.name == "ComprehensiveServer"
        assert info.instructions == "A server with everything"
        assert info.fastmcp_version == fastmcp.__version__

        # Check all components are present
        assert len(info.tools) == 1
        tool_names = [tool.name for tool in info.tools]
        assert "calculate" in tool_names

        assert len(info.resources) == 1
        resource_uris = [res.uri for res in info.resources]
        assert "resource://data" in resource_uris

        assert len(info.templates) == 1
        template_uris = [tmpl.uri_template for tmpl in info.templates]
        assert "resource://item/{id}" in template_uris

        assert len(info.prompts) == 1
        prompt_names = [prompt.name for prompt in info.prompts]
        assert "analyze" in prompt_names

        # Check capabilities
        assert "tools" in info.capabilities
        assert "resources" in info.capabilities
        assert "prompts" in info.capabilities
        assert "logging" in info.capabilities

    async def test_server_no_instructions(self):
        """Test get_fastmcp_info with a server that has no instructions."""
        mcp = FastMCP("NoInstructionsServer")

        info = await inspect_fastmcp(mcp)

        assert info.name == "NoInstructionsServer"
        assert info.instructions is None

    async def test_server_with_client_integration(self):
        """Test that the extracted info matches what a client would see."""
        mcp = FastMCP("IntegrationServer")

        @mcp.tool
        def test_tool() -> str:
            return "test"

        @mcp.resource("resource://test")
        def test_resource() -> str:
            return "test resource"

        @mcp.prompt
        def test_prompt() -> list:
            return [{"role": "user", "content": "test"}]

        # Get info using our function
        info = await inspect_fastmcp(mcp)

        # Verify using client
        async with Client(mcp) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()

            assert len(info.tools) == len(tools)
            assert len(info.resources) == len(resources)
            assert len(info.prompts) == len(prompts)

            assert info.tools[0].name == tools[0].name
            assert info.resources[0].uri == str(resources[0].uri)
            assert info.prompts[0].name == prompts[0].name

    async def test_inspect_respects_tag_filtering(self):
        """Test that inspect omits components filtered out by include_tags/exclude_tags.

        Regression test for Issue #2032: inspect command was showing components
        that were filtered out by tag rules, causing confusion when those
        components weren't actually available to clients.
        """
        # Create server with include_tags that will filter out untagged components
        mcp = FastMCP(
            "FilteredServer",
            include_tags={"fetch", "analyze", "create"},
        )

        # Add tools with and without matching tags
        @mcp.tool(tags={"fetch"})
        def tagged_tool() -> str:
            """Tool with matching tag - should be visible."""
            return "visible"

        @mcp.tool
        def untagged_tool() -> str:
            """Tool without tags - should be filtered out."""
            return "hidden"

        # Add resources with and without matching tags
        @mcp.resource("resource://tagged", tags={"analyze"})
        def tagged_resource() -> str:
            """Resource with matching tag - should be visible."""
            return "visible resource"

        @mcp.resource("resource://untagged")
        def untagged_resource() -> str:
            """Resource without tags - should be filtered out."""
            return "hidden resource"

        # Add templates with and without matching tags
        @mcp.resource("resource://tagged/{id}", tags={"create"})
        def tagged_template(id: str) -> str:
            """Template with matching tag - should be visible."""
            return f"visible template {id}"

        @mcp.resource("resource://untagged/{id}")
        def untagged_template(id: str) -> str:
            """Template without tags - should be filtered out."""
            return f"hidden template {id}"

        # Add prompts with and without matching tags
        @mcp.prompt(tags={"fetch"})
        def tagged_prompt() -> list:
            """Prompt with matching tag - should be visible."""
            return [{"role": "user", "content": "visible prompt"}]

        @mcp.prompt
        def untagged_prompt() -> list:
            """Prompt without tags - should be filtered out."""
            return [{"role": "user", "content": "hidden prompt"}]

        # Get inspect info
        info = await inspect_fastmcp(mcp)

        # Verify only tagged components are visible
        assert len(info.tools) == 1
        assert info.tools[0].name == "tagged_tool"

        assert len(info.resources) == 1
        assert info.resources[0].uri == "resource://tagged"

        assert len(info.templates) == 1
        assert info.templates[0].uri_template == "resource://tagged/{id}"

        assert len(info.prompts) == 1
        assert info.prompts[0].name == "tagged_prompt"

        # Verify this matches what a client would see
        async with Client(mcp) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            prompts = await client.list_prompts()

            assert len(info.tools) == len(tools)
            assert len(info.resources) == len(resources)
            assert len(info.templates) == len(templates)
            assert len(info.prompts) == len(prompts)

    async def test_inspect_respects_tag_filtering_with_mounted_servers(self):
        """Test that inspect applies tag filtering to mounted servers.

        Verifies that when a parent server has tag filters, those filters
        are respected when inspecting components from mounted servers.
        """
        # Create a mounted server with various tagged and untagged components
        mounted = FastMCP("MountedServer")

        @mounted.tool(tags={"allowed"})
        def allowed_tool() -> str:
            return "allowed"

        @mounted.tool(tags={"blocked"})
        def blocked_tool() -> str:
            return "blocked"

        @mounted.tool
        def untagged_tool() -> str:
            return "untagged"

        @mounted.resource("resource://allowed", tags={"allowed"})
        def allowed_resource() -> str:
            return "allowed resource"

        @mounted.resource("resource://blocked", tags={"blocked"})
        def blocked_resource() -> str:
            return "blocked resource"

        @mounted.prompt(tags={"allowed"})
        def allowed_prompt() -> list:
            return [{"role": "user", "content": "allowed"}]

        @mounted.prompt(tags={"blocked"})
        def blocked_prompt() -> list:
            return [{"role": "user", "content": "blocked"}]

        # Create parent server with tag filtering
        parent = FastMCP("ParentServer", include_tags={"allowed"})
        parent.mount(mounted)

        # Get inspect info
        info = await inspect_fastmcp(parent)

        # Only components with "allowed" tag should be visible
        tool_names = [t.name for t in info.tools]
        assert "allowed_tool" in tool_names
        assert "blocked_tool" not in tool_names
        assert "untagged_tool" not in tool_names

        resource_uris = [r.uri for r in info.resources]
        assert "resource://allowed" in resource_uris
        assert "resource://blocked" not in resource_uris

        prompt_names = [p.name for p in info.prompts]
        assert "allowed_prompt" in prompt_names
        assert "blocked_prompt" not in prompt_names

        # Verify this matches what a client would see
        async with Client(parent) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()

            assert len(info.tools) == len(tools)
            assert len(info.resources) == len(resources)
            assert len(info.prompts) == len(prompts)

    async def test_inspect_parent_filters_override_mounted_server_filters(self):
        """Test that parent server tag filters apply to mounted servers.

        Even if a mounted server has no tag filters of its own,
        the parent server's filters should still apply.
        """
        # Create mounted server with NO tag filters (allows everything)
        mounted = FastMCP("MountedServer")

        @mounted.tool(tags={"production"})
        def production_tool() -> str:
            return "production"

        @mounted.tool(tags={"development"})
        def development_tool() -> str:
            return "development"

        @mounted.tool
        def untagged_tool() -> str:
            return "untagged"

        # Create parent with exclude_tags - should filter mounted components
        parent = FastMCP("ParentServer", exclude_tags={"development"})
        parent.mount(mounted)

        # Get inspect info
        info = await inspect_fastmcp(parent)

        # Only production and untagged should be visible
        tool_names = [t.name for t in info.tools]
        assert "production_tool" in tool_names
        assert "untagged_tool" in tool_names
        assert "development_tool" not in tool_names

        # Verify this matches what a client would see
        async with Client(parent) as client:
            tools = await client.list_tools()
            assert len(info.tools) == len(tools)


class TestFastMCP1xCompatibility:
    """Tests for FastMCP 1.x compatibility."""

    async def test_fastmcp1x_empty_server(self):
        """Test get_fastmcp_info_v1 with an empty FastMCP1x server."""
        mcp = FastMCP1x("Test1x")

        info = await inspect_fastmcp_v1(mcp)

        assert info.name == "Test1x"
        assert info.instructions is None
        assert info.fastmcp_version == fastmcp.__version__  # CLI version
        assert info.mcp_version == importlib.metadata.version("mcp")
        assert info.server_generation == 1  # v1 server
        assert info.version is None
        assert info.tools == []
        assert info.prompts == []
        assert info.resources == []
        assert info.templates == []  # No templates added in this test
        assert "tools" in info.capabilities

    async def test_fastmcp1x_with_tools(self):
        """Test get_fastmcp_info_v1 with a FastMCP1x server that has tools."""
        mcp = FastMCP1x("Test1x")

        @mcp.tool()
        def add_numbers(a: int, b: int) -> int:
            return a + b

        @mcp.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        info = await inspect_fastmcp_v1(mcp)

        assert info.name == "Test1x"
        assert len(info.tools) == 2
        tool_names = [tool.name for tool in info.tools]
        assert "add_numbers" in tool_names
        assert "greet" in tool_names

    async def test_fastmcp1x_with_resources(self):
        """Test get_fastmcp_info_v1 with a FastMCP1x server that has resources."""
        mcp = FastMCP1x("Test1x")

        @mcp.resource("resource://data")
        def get_data() -> str:
            return "Some data"

        info = await inspect_fastmcp_v1(mcp)

        assert info.name == "Test1x"
        assert len(info.resources) == 1
        resource_uris = [res.uri for res in info.resources]
        assert "resource://data" in resource_uris
        assert len(info.templates) == 0  # No templates added in this test
        assert info.server_generation == 1  # v1 server

    async def test_fastmcp1x_with_prompts(self):
        """Test get_fastmcp_info_v1 with a FastMCP1x server that has prompts."""
        mcp = FastMCP1x("Test1x")

        @mcp.prompt("analyze")
        def analyze_data(data: str) -> list:
            return [{"role": "user", "content": f"Analyze: {data}"}]

        info = await inspect_fastmcp_v1(mcp)

        assert info.name == "Test1x"
        assert len(info.prompts) == 1
        prompt_names = [prompt.name for prompt in info.prompts]
        assert "analyze" in prompt_names

    async def test_dispatcher_with_fastmcp1x(self):
        """Test that the main get_fastmcp_info function correctly dispatches to v1."""
        mcp = FastMCP1x("Test1x")

        @mcp.tool()
        def test_tool() -> str:
            return "test"

        info = await inspect_fastmcp(mcp)

        assert info.name == "Test1x"
        assert len(info.tools) == 1
        tool_names = [tool.name for tool in info.tools]
        assert "test_tool" in tool_names
        assert len(info.templates) == 0  # No templates added in this test
        assert info.server_generation == 1  # v1 server

    async def test_dispatcher_with_fastmcp2x(self):
        """Test that the main get_fastmcp_info function correctly dispatches to v2."""
        mcp = FastMCP("Test2x")

        @mcp.tool
        def test_tool() -> str:
            return "test"

        info = await inspect_fastmcp(mcp)

        assert info.name == "Test2x"
        assert len(info.tools) == 1
        tool_names = [tool.name for tool in info.tools]
        assert "test_tool" in tool_names

    async def test_fastmcp1x_vs_fastmcp2x_comparison(self):
        """Test that both versions can be inspected and compared."""
        mcp1x = FastMCP1x("Test1x")
        mcp2x = FastMCP("Test2x")

        @mcp1x.tool()
        def tool1x() -> str:
            return "1x"

        @mcp2x.tool
        def tool2x() -> str:
            return "2x"

        info1x = await inspect_fastmcp(mcp1x)
        info2x = await inspect_fastmcp(mcp2x)

        assert info1x.name == "Test1x"
        assert info2x.name == "Test2x"
        assert len(info1x.tools) == 1
        assert len(info2x.tools) == 1

        tool1x_names = [tool.name for tool in info1x.tools]
        tool2x_names = [tool.name for tool in info2x.tools]
        assert "tool1x" in tool1x_names
        assert "tool2x" in tool2x_names

        # Check server versions
        assert info1x.server_generation == 1  # v1
        assert info2x.server_generation == 2  # v2
        assert info1x.version is None
        assert info2x.version == fastmcp.__version__

        # No templates added in these tests
        assert len(info1x.templates) == 0
        assert len(info2x.templates) == 0


class TestIconExtraction:
    """Tests for icon extraction in inspect."""

    async def test_server_icons_and_website(self):
        """Test that server-level icons and website_url are extracted."""
        from mcp.types import Icon

        mcp = FastMCP(
            "IconServer",
            website_url="https://example.com",
            icons=[
                Icon(
                    src="https://example.com/icon.png",
                    mimeType="image/png",
                    sizes=["48x48"],
                )
            ],
        )

        info = await inspect_fastmcp(mcp)

        assert info.website_url == "https://example.com"
        assert info.icons is not None
        assert len(info.icons) == 1
        assert info.icons[0]["src"] == "https://example.com/icon.png"
        assert info.icons[0]["mimeType"] == "image/png"
        assert info.icons[0]["sizes"] == ["48x48"]

    async def test_server_without_icons(self):
        """Test that servers without icons have None for icons and website_url."""
        mcp = FastMCP("NoIconServer")

        info = await inspect_fastmcp(mcp)

        assert info.website_url is None
        assert info.icons is None

    async def test_tool_icons(self):
        """Test that tool icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("ToolIconServer")

        @mcp.tool(
            icons=[
                Icon(
                    src="https://example.com/calculator.png",
                    mimeType="image/png",
                )
            ]
        )
        def calculate(x: int) -> int:
            """Calculate something."""
            return x * 2

        @mcp.tool
        def no_icon_tool() -> str:
            """Tool without icon."""
            return "no icon"

        info = await inspect_fastmcp(mcp)

        assert len(info.tools) == 2

        # Find the calculate tool
        calculate_tool = next(t for t in info.tools if t.name == "calculate")
        assert calculate_tool.icons is not None
        assert len(calculate_tool.icons) == 1
        assert calculate_tool.icons[0]["src"] == "https://example.com/calculator.png"

        # Find the no_icon tool
        no_icon = next(t for t in info.tools if t.name == "no_icon_tool")
        assert no_icon.icons is None

    async def test_resource_icons(self):
        """Test that resource icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("ResourceIconServer")

        @mcp.resource(
            "resource://data",
            icons=[Icon(src="https://example.com/data.png", mimeType="image/png")],
        )
        def get_data() -> str:
            """Get data."""
            return "data"

        @mcp.resource("resource://no-icon")
        def get_no_icon() -> str:
            """Get data without icon."""
            return "no icon"

        info = await inspect_fastmcp(mcp)

        assert len(info.resources) == 2

        # Find the data resource
        data_resource = next(r for r in info.resources if r.uri == "resource://data")
        assert data_resource.icons is not None
        assert len(data_resource.icons) == 1
        assert data_resource.icons[0]["src"] == "https://example.com/data.png"

        # Find the no-icon resource
        no_icon = next(r for r in info.resources if r.uri == "resource://no-icon")
        assert no_icon.icons is None

    async def test_template_icons(self):
        """Test that resource template icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("TemplateIconServer")

        @mcp.resource(
            "resource://user/{id}",
            icons=[Icon(src="https://example.com/user.png", mimeType="image/png")],
        )
        def get_user(id: str) -> str:
            """Get user by ID."""
            return f"user {id}"

        @mcp.resource("resource://item/{id}")
        def get_item(id: str) -> str:
            """Get item without icon."""
            return f"item {id}"

        info = await inspect_fastmcp(mcp)

        assert len(info.templates) == 2

        # Find the user template
        user_template = next(
            t for t in info.templates if t.uri_template == "resource://user/{id}"
        )
        assert user_template.icons is not None
        assert len(user_template.icons) == 1
        assert user_template.icons[0]["src"] == "https://example.com/user.png"

        # Find the no-icon template
        no_icon = next(
            t for t in info.templates if t.uri_template == "resource://item/{id}"
        )
        assert no_icon.icons is None

    async def test_prompt_icons(self):
        """Test that prompt icons are extracted."""
        from mcp.types import Icon

        mcp = FastMCP("PromptIconServer")

        @mcp.prompt(
            icons=[Icon(src="https://example.com/analyze.png", mimeType="image/png")]
        )
        def analyze(data: str) -> list:
            """Analyze data."""
            return [{"role": "user", "content": f"Analyze: {data}"}]

        @mcp.prompt
        def no_icon_prompt(text: str) -> list:
            """Prompt without icon."""
            return [{"role": "user", "content": text}]

        info = await inspect_fastmcp(mcp)

        assert len(info.prompts) == 2

        # Find the analyze prompt
        analyze_prompt = next(p for p in info.prompts if p.name == "analyze")
        assert analyze_prompt.icons is not None
        assert len(analyze_prompt.icons) == 1
        assert analyze_prompt.icons[0]["src"] == "https://example.com/analyze.png"

        # Find the no-icon prompt
        no_icon = next(p for p in info.prompts if p.name == "no_icon_prompt")
        assert no_icon.icons is None

    async def test_multiple_icons(self):
        """Test that components with multiple icons extract all of them."""
        from mcp.types import Icon

        mcp = FastMCP(
            "MultiIconServer",
            icons=[
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
            ],
        )

        @mcp.tool(
            icons=[
                Icon(src="https://example.com/tool-small.png", sizes=["24x24"]),
                Icon(src="https://example.com/tool-large.png", sizes=["48x48"]),
            ]
        )
        def multi_icon_tool() -> str:
            """Tool with multiple icons."""
            return "multi"

        info = await inspect_fastmcp(mcp)

        # Check server icons
        assert info.icons is not None
        assert len(info.icons) == 2
        assert info.icons[0]["sizes"] == ["48x48"]
        assert info.icons[1]["sizes"] == ["96x96"]

        # Check tool icons
        assert len(info.tools) == 1
        assert info.tools[0].icons is not None
        assert len(info.tools[0].icons) == 2
        assert info.tools[0].icons[0]["sizes"] == ["24x24"]
        assert info.tools[0].icons[1]["sizes"] == ["48x48"]

    async def test_data_uri_icons(self):
        """Test that data URI icons are extracted correctly."""
        from mcp.types import Icon

        data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        mcp = FastMCP("DataURIServer")

        @mcp.tool(icons=[Icon(src=data_uri, mimeType="image/png")])
        def data_uri_tool() -> str:
            """Tool with data URI icon."""
            return "data"

        info = await inspect_fastmcp(mcp)

        assert len(info.tools) == 1
        assert info.tools[0].icons is not None
        assert info.tools[0].icons[0]["src"] == data_uri
        assert info.tools[0].icons[0]["mimeType"] == "image/png"

    async def test_icons_in_fastmcp_v1(self):
        """Test that icons are extracted from FastMCP 1.x servers."""
        from mcp.types import Icon

        mcp = FastMCP1x("Icon1xServer")

        @mcp.tool(
            icons=[Icon(src="https://example.com/v1-tool.png", mimeType="image/png")]
        )
        def v1_tool() -> str:
            """Tool in v1 server."""
            return "v1"

        info = await inspect_fastmcp_v1(mcp)

        assert len(info.tools) == 1
        # v1 servers should also extract icons if present
        if info.tools[0].icons is not None:
            assert info.tools[0].icons[0]["src"] == "https://example.com/v1-tool.png"

    async def test_icons_in_formatted_output(self):
        """Test that icons appear in formatted JSON output."""
        from mcp.types import Icon

        mcp = FastMCP(
            "FormattedIconServer",
            website_url="https://example.com",
            icons=[Icon(src="https://example.com/server.png", mimeType="image/png")],
        )

        @mcp.tool(
            icons=[Icon(src="https://example.com/tool.png", mimeType="image/png")]
        )
        def icon_tool() -> str:
            """Tool with icon."""
            return "icon"

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        import json

        data = json.loads(json_bytes)

        # Check server icons in formatted output
        assert data["server"]["website_url"] == "https://example.com"
        assert data["server"]["icons"] is not None
        assert len(data["server"]["icons"]) == 1
        assert data["server"]["icons"][0]["src"] == "https://example.com/server.png"

        # Check tool icons in formatted output
        assert len(data["tools"]) == 1
        assert data["tools"][0]["icons"] is not None
        assert len(data["tools"][0]["icons"]) == 1
        assert data["tools"][0]["icons"][0]["src"] == "https://example.com/tool.png"

    async def test_icons_always_present_in_json(self):
        """Test that icons and website_url fields are always present in JSON, even when None."""
        mcp = FastMCP("AlwaysPresentServer")

        @mcp.tool
        def no_icon() -> str:
            """Tool without icon."""
            return "none"

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        import json

        data = json.loads(json_bytes)

        # Fields should always be present, even when None
        assert "website_url" in data["server"]
        assert "icons" in data["server"]
        assert data["server"]["website_url"] is None
        assert data["server"]["icons"] is None

        assert len(data["tools"]) == 1
        assert "icons" in data["tools"][0]
        assert data["tools"][0]["icons"] is None


class TestFormatFunctions:
    """Tests for the formatting functions."""

    async def test_format_fastmcp_info(self):
        """Test formatting as FastMCP-specific JSON."""
        mcp = FastMCP("TestServer", instructions="Test instructions", version="1.2.3")

        @mcp.tool
        def test_tool(x: int) -> dict:
            """A test tool."""
            return {"result": x * 2}

        info = await inspect_fastmcp(mcp)
        json_bytes = format_fastmcp_info(info)

        # Verify it's valid JSON
        import json

        data = json.loads(json_bytes)

        # Check FastMCP-specific fields are present
        assert "server" in data
        assert data["server"]["name"] == "TestServer"
        assert data["server"]["instructions"] == "Test instructions"
        assert data["server"]["generation"] == 2  # v2 server
        assert data["server"]["version"] == "1.2.3"
        assert "capabilities" in data["server"]

        # Check environment information
        assert "environment" in data
        assert data["environment"]["fastmcp"] == fastmcp.__version__
        assert data["environment"]["mcp"] == importlib.metadata.version("mcp")

        # Check tools
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "test_tool"
        assert "tags" in data["tools"][0]

    async def test_format_mcp_info(self):
        """Test formatting as MCP protocol JSON."""
        mcp = FastMCP("TestServer", instructions="Test instructions", version="2.0.0")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @mcp.prompt
        def test_prompt(name: str) -> list:
            """Test prompt."""
            return [{"role": "user", "content": f"Hello {name}"}]

        json_bytes = await format_mcp_info(mcp)

        # Verify it's valid JSON
        import json

        data = json.loads(json_bytes)

        # Check MCP protocol structure with camelCase
        assert "serverInfo" in data
        assert data["serverInfo"]["name"] == "TestServer"

        # Check server version in MCP format
        assert data["serverInfo"]["version"] == "2.0.0"

        # MCP format SHOULD have environment fields
        assert "environment" in data
        assert data["environment"]["fastmcp"] == fastmcp.__version__
        assert data["environment"]["mcp"] == importlib.metadata.version("mcp")
        assert "capabilities" in data

        assert "tools" in data
        assert "prompts" in data
        assert "resources" in data
        assert "resourceTemplates" in data

        # Check tools have MCP format (camelCase fields)
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "add"
        assert "inputSchema" in data["tools"][0]

        # FastMCP-specific fields should not be present
        assert "tags" not in data["tools"][0]
        assert "enabled" not in data["tools"][0]

    async def test_format_info_with_fastmcp_format(self):
        """Test format_info with fastmcp format."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        # Test with string format
        json_bytes = await format_info(mcp, "fastmcp")
        import json

        data = json.loads(json_bytes)
        assert data["server"]["name"] == "TestServer"
        assert "tags" in data["tools"][0]  # FastMCP-specific field

        # Test with enum format
        json_bytes = await format_info(mcp, InspectFormat.FASTMCP)
        data = json.loads(json_bytes)
        assert data["server"]["name"] == "TestServer"

    async def test_format_info_with_mcp_format(self):
        """Test format_info with mcp format."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        json_bytes = await format_info(mcp, "mcp")

        import json

        data = json.loads(json_bytes)
        assert "serverInfo" in data
        assert "tools" in data
        assert "inputSchema" in data["tools"][0]  # MCP uses camelCase

    async def test_format_info_requires_format(self):
        """Test that format_info requires a format parameter."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def test() -> str:
            return "test"

        # Should work with valid formats
        json_bytes = await format_info(mcp, "fastmcp")
        assert json_bytes

        json_bytes = await format_info(mcp, "mcp")
        assert json_bytes

        # Should fail with invalid format
        import pytest

        with pytest.raises(ValueError, match="not a valid InspectFormat"):
            await format_info(mcp, "invalid")  # type: ignore

    async def test_tool_with_output_schema(self):
        """Test that output_schema is properly extracted and included."""
        mcp = FastMCP("TestServer")

        @mcp.tool(
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "number"},
                    "message": {"type": "string"},
                },
            }
        )
        def compute(x: int) -> dict:
            """Compute something."""
            return {"result": x * 2, "message": f"Doubled {x}"}

        info = await inspect_fastmcp(mcp)

        # Check output_schema is captured
        assert len(info.tools) == 1
        assert info.tools[0].output_schema is not None
        assert info.tools[0].output_schema["type"] == "object"
        assert "result" in info.tools[0].output_schema["properties"]

        # Verify it's included in FastMCP format
        json_bytes = format_fastmcp_info(info)
        import json

        data = json.loads(json_bytes)
        # Tools are at the top level, not nested
        assert data["tools"][0]["output_schema"]["type"] == "object"
