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
        json_bytes = await format_fastmcp_info(info)

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
        assert data["tools"][0]["enabled"] is True
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
        json_bytes = await format_fastmcp_info(info)
        import json

        data = json.loads(json_bytes)
        # Tools are at the top level, not nested
        assert data["tools"][0]["output_schema"]["type"] == "object"
