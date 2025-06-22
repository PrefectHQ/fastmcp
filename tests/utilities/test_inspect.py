"""Tests for the inspect.py module."""

# Import FastMCP1x for testing (always available since mcp is a dependency)
from mcp.server.fastmcp import FastMCP as FastMCP1x

import fastmcp
from fastmcp import Client, FastMCP
from fastmcp.utilities.inspect import (
    FastMCPInfo,
    ToolInfo,
    _is_fastmcp_v1,
    inspect_fastmcp,
    inspect_fastmcp_v1,
)


class TestFastMCPInfo:
    """Tests for the FastMCPInfo dataclass."""

    def test_fastmcp_info_creation(self):
        """Test that FastMCPInfo can be created with all required fields."""
        tool = ToolInfo(
            key="tool1", name="tool1", description="Test tool", input_schema={}
        )
        info = FastMCPInfo(
            name="TestServer",
            instructions="Test instructions",
            fastmcp_version="1.0.0",
            mcp_version="1.0.0",
            server_version="1.0.0",
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
        assert info.server_version == "1.0.0"
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
            server_version="1.0.0",
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
        mcp = FastMCP("EmptyServer", instructions="Empty server for testing")

        info = await inspect_fastmcp(mcp)

        assert info.name == "EmptyServer"
        assert info.instructions == "Empty server for testing"
        assert info.fastmcp_version == fastmcp.__version__
        assert info.mcp_version is not None
        assert info.server_version == fastmcp.__version__  # v2.x uses FastMCP version
        assert info.tools == []
        assert info.prompts == []
        assert info.resources == []
        assert info.templates == []
        assert "tools" in info.capabilities
        assert "resources" in info.capabilities
        assert "prompts" in info.capabilities
        assert "logging" in info.capabilities

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


class TestFastMCP1xCompatibility:
    """Tests for FastMCP 1.x compatibility."""

    async def test_fastmcp1x_detection(self):
        """Test that FastMCP1x instances are correctly detected."""
        mcp1x = FastMCP1x("Test1x")
        mcp2x = FastMCP("Test2x")

        assert _is_fastmcp_v1(mcp1x) is True
        assert _is_fastmcp_v1(mcp2x) is False

    async def test_fastmcp1x_empty_server(self):
        """Test get_fastmcp_info_v1 with an empty FastMCP1x server."""
        mcp = FastMCP1x("Test1x")

        info = await inspect_fastmcp_v1(mcp)

        assert info.name == "Test1x"
        assert info.instructions is None
        assert info.fastmcp_version == fastmcp.__version__
        assert info.mcp_version is not None
        assert info.server_version == "1.0"  # v1.x servers use "1.0"
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
        assert info1x.server_version == "1.0"
        assert info2x.server_version == fastmcp.__version__

        # No templates added in these tests
        assert len(info1x.templates) == 0
        assert len(info2x.templates) == 0
