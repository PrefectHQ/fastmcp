from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

import pytest
from mcp.types import TextContent, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools import FunctionTool
from fastmcp.tools.tool import Tool


class TestCreateServer:
    async def test_create_server(self):
        mcp = FastMCP(instructions="Server instructions")
        assert mcp.name.startswith("FastMCP-")
        assert mcp.instructions == "Server instructions"

    async def test_change_instruction(self):
        mcp = FastMCP(instructions="Server instructions")
        assert mcp.instructions == "Server instructions"
        mcp.instructions = "New instructions"
        assert mcp.instructions == "New instructions"

    async def test_non_ascii_description(self):
        """Test that FastMCP handles non-ASCII characters in descriptions correctly"""
        mcp = FastMCP()

        @mcp.tool(
            description=(
                "🌟 This tool uses emojis and UTF-8 characters: á é í ó ú ñ 漢字 🎉"
            )
        )
        def hello_world(name: str = "世界") -> str:
            return f"¡Hola, {name}! 👋"

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1
            tool = tools[0]
            assert tool.description is not None
            assert "🌟" in tool.description
            assert "漢字" in tool.description
            assert "🎉" in tool.description

            result = await client.call_tool("hello_world", {})
            assert result.data == "¡Hola, 世界! 👋"


class TestTools:
    async def test_mcp_tool_name(self):
        """Test MCPTool name for add_tool (key != tool.name)."""

        mcp = FastMCP()

        @mcp.tool
        def fn(x: int) -> int:
            return x + 1

        mcp_tools = await mcp._list_tools_mcp()
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "fn"

    async def test_mcp_tool_custom_name(self):
        """Test MCPTool name for add_tool (key != tool.name)."""

        mcp = FastMCP()

        @mcp.tool(name="custom_name")
        def fn(x: int) -> int:
            return x + 1

        mcp_tools = await mcp._list_tools_mcp()
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "custom_name"

    async def test_remove_tool_successfully(self):
        """Test that FastMCP.remove_tool removes the tool from the registry."""

        mcp = FastMCP()

        @mcp.tool(name="adder")
        def add(a: int, b: int) -> int:
            return a + b

        mcp_tools = await mcp.get_tools()
        assert "adder" in mcp_tools

        mcp.remove_tool("adder")
        mcp_tools = await mcp.get_tools()
        assert "adder" not in mcp_tools

        with pytest.raises(NotFoundError, match="Unknown tool: adder"):
            await mcp._call_tool_mcp("adder", {"a": 1, "b": 2})

    async def test_add_tool_at_init(self):
        def f(x: int) -> int:
            return x + 1

        def g(x: int) -> int:
            """add two to a number"""
            return x + 2

        g_tool = FunctionTool.from_function(g, name="g-tool")

        mcp = FastMCP(tools=[f, g_tool])

        tools = await mcp.get_tools()
        assert len(tools) == 2
        assert tools["f"].name == "f"
        assert tools["g-tool"].name == "g-tool"
        assert tools["g-tool"].description == "add two to a number"


class TestServerDelegation:
    """Test that FastMCP properly delegates to LocalProvider."""

    async def test_tool_decorator_delegates_to_local_provider(self):
        """Test that @mcp.tool registers with the local provider."""
        mcp = FastMCP()

        @mcp.tool
        def my_tool() -> str:
            return "result"

        # Verify the tool is in the local provider
        tool = await mcp._local_provider.get_tool("my_tool")
        assert tool is not None
        assert tool.name == "my_tool"

    async def test_resource_decorator_delegates_to_local_provider(self):
        """Test that @mcp.resource registers with the local provider."""
        mcp = FastMCP()

        @mcp.resource("resource://test")
        def my_resource() -> str:
            return "content"

        # Verify the resource is in the local provider
        resource = await mcp._local_provider.get_resource("resource://test")
        assert resource is not None

    async def test_prompt_decorator_delegates_to_local_provider(self):
        """Test that @mcp.prompt registers with the local provider."""
        mcp = FastMCP()

        @mcp.prompt
        def my_prompt() -> str:
            return "prompt content"

        # Verify the prompt is in the local provider
        prompt = await mcp._local_provider.get_prompt("my_prompt")
        assert prompt is not None
        assert prompt.name == "my_prompt"

    async def test_add_tool_delegates_to_local_provider(self):
        """Test that mcp.add_tool() registers with the local provider."""
        mcp = FastMCP()

        def standalone_tool() -> str:
            return "result"

        mcp.add_tool(FunctionTool.from_function(standalone_tool))

        # Verify the tool is in the local provider
        tool = await mcp._local_provider.get_tool("standalone_tool")
        assert tool is not None
        assert tool.name == "standalone_tool"

    async def test_get_tools_includes_local_provider_tools(self):
        """Test that get_tools() returns tools from local provider."""
        mcp = FastMCP()

        @mcp.tool
        def local_tool() -> str:
            return "local"

        tools = await mcp.get_tools()
        assert "local_tool" in tools


class TestResourcePrefixMounting:
    """Test resource prefixing in mounted servers."""

    async def test_mounted_server_resource_prefixing(self):
        """Test that resources in mounted servers use the correct prefix format."""
        # Create a server with resources
        server = FastMCP(name="ResourceServer")

        @server.resource("resource://test-resource")
        def get_resource():
            return "Resource content"

        @server.resource("resource:///absolute/path")
        def get_absolute_resource():
            return "Absolute resource content"

        @server.resource("resource://{param}/template")
        def get_template_resource(param: str):
            return f"Template resource with {param}"

        # Create a main server and mount the resource server
        main_server = FastMCP(name="MainServer")
        main_server.mount(server, "prefix")

        # Check that the resources are mounted with the correct prefixes
        resources = await main_server.get_resources()
        templates = await main_server.get_resource_templates()

        assert "resource://prefix/test-resource" in resources
        assert "resource://prefix//absolute/path" in resources
        assert "resource://prefix/{param}/template" in templates

        # Test that prefixed resources can be accessed
        async with Client(main_server) as client:
            # Regular resource
            result = await client.read_resource("resource://prefix/test-resource")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Resource content"

            # Absolute path resource
            result = await client.read_resource("resource://prefix//absolute/path")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Absolute resource content"

            # Template resource
            result = await client.read_resource(
                "resource://prefix/param-value/template"
            )
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource with param-value"


class TestShouldIncludeComponent:
    def test_no_filters_returns_true(self):
        """Test that when no include or exclude filters are provided, always returns True."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2"}, parameters={})
        mcp = FastMCP(tools=[tool])
        result = mcp._should_enable_component(tool)
        assert result is True

    def test_exclude_string_tag_present_returns_false(self):
        """Test that when an exclude string tag is present in tags, returns False."""
        tool = Tool(
            name="test_tool", tags={"tag1", "tag2", "exclude_me"}, parameters={}
        )
        mcp = FastMCP(tools=[tool], exclude_tags={"exclude_me"})
        result = mcp._should_enable_component(tool)
        assert result is False

    def test_exclude_string_tag_absent_returns_true(self):
        """Test that when an exclude string tag is not present in tags, returns True."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2"}, parameters={})
        mcp = FastMCP(tools=[tool], exclude_tags={"exclude_me"})
        result = mcp._should_enable_component(tool)
        assert result is True

    def test_multiple_exclude_tags_any_match_returns_false(self):
        """Test that when any exclude tag matches, returns False."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2", "tag3"}, parameters={})
        mcp = FastMCP(
            tools=[tool], exclude_tags={"not_present", "tag2", "also_not_present"}
        )
        result = mcp._should_enable_component(tool)
        assert result is False

    def test_include_string_tag_present_returns_true(self):
        """Test that when an include string tag is present in tags, returns True."""
        tool = Tool(
            name="test_tool", tags={"tag1", "include_me", "tag2"}, parameters={}
        )
        mcp = FastMCP(tools=[tool], include_tags={"include_me"})
        result = mcp._should_enable_component(tool)
        assert result is True

    def test_include_string_tag_absent_returns_false(self):
        """Test that when an include string tag is not present in tags, returns False."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2"}, parameters={})
        mcp = FastMCP(tools=[tool], include_tags={"include_me"})
        result = mcp._should_enable_component(tool)
        assert result is False

    def test_multiple_include_tags_any_match_returns_true(self):
        """Test that when any include tag matches, returns True."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2", "tag3"}, parameters={})
        mcp = FastMCP(
            tools=[tool], include_tags={"not_present", "tag2", "also_not_present"}
        )
        result = mcp._should_enable_component(tool)
        assert result is True

    def test_multiple_include_tags_none_match_returns_false(self):
        """Test that when no include tags match, returns False."""
        tool = Tool(name="test_tool", tags={"tag1", "tag2", "tag3"}, parameters={})
        mcp = FastMCP(tools=[tool], include_tags={"not_present", "also_not_present"})
        result = mcp._should_enable_component(tool)
        assert result is False

    def test_exclude_takes_precedence_over_include(self):
        """Test that exclude tags take precedence over include tags."""
        tool = Tool(
            name="test_tool", tags={"tag1", "tag2", "exclude_me"}, parameters={}
        )
        mcp = FastMCP(tools=[tool], include_tags={"tag1"}, exclude_tags={"exclude_me"})
        result = mcp._should_enable_component(tool)
        assert result is False

    def test_empty_include_exclude_sets(self):
        """Test behavior with empty include/exclude sets."""
        # Empty include set means nothing matches
        tool1 = Tool(name="test_tool", tags={"tag1", "tag2"}, parameters={})
        mcp1 = FastMCP(tools=[tool1], include_tags=set())
        result = mcp1._should_enable_component(tool1)
        assert result is False

        # Empty exclude set means nothing excluded
        tool2 = Tool(name="test_tool", tags={"tag1", "tag2"}, parameters={})
        mcp2 = FastMCP(tools=[tool2], exclude_tags=set())
        result = mcp2._should_enable_component(tool2)
        assert result is True

    def test_empty_tags_with_filters(self):
        """Test behavior when input tags are empty."""
        # With include filters, empty tags should not match
        tool1 = Tool(name="test_tool", tags=set(), parameters={})
        mcp1 = FastMCP(tools=[tool1], include_tags={"required_tag"})
        result = mcp1._should_enable_component(tool1)
        assert result is False

        # With exclude filters but no include, empty tags should pass
        tool2 = Tool(name="test_tool", tags=set(), parameters={})
        mcp2 = FastMCP(tools=[tool2], exclude_tags={"bad_tag"})
        result = mcp2._should_enable_component(tool2)
        assert result is True


class TestSettingsFromEnvironment:
    async def test_settings_from_environment_issue_1749(self):
        """Test that when auth is enabled, the server starts."""
        from fastmcp.client.transports import PythonStdioTransport
        from fastmcp.server.auth.providers.azure import AzureProvider
        from fastmcp.settings import Settings

        script = dedent("""
        import os

        os.environ["FASTMCP_SERVER_AUTH"] = "fastmcp.server.auth.providers.azure.AzureProvider"

        os.environ["FASTMCP_SERVER_AUTH_AZURE_TENANT_ID"] = "A_Valid_Value"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_CLIENT_ID"] = "A_Valid_Value"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_CLIENT_SECRET"] = "A_Valid_Value"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_REDIRECT_PATH"] = "/auth/callback"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_BASE_URL"] = "http://localhost:8000"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_REQUIRED_SCOPES"] = "User.Read,email,profile"
        os.environ["FASTMCP_SERVER_AUTH_AZURE_JWT_SIGNING_KEY"] = "test-secret"

        import fastmcp
        
        mcp = fastmcp.FastMCP("TestServer")

        mcp.run()
        """)

        with TemporaryDirectory() as temp_dir:
            server_file = Path(temp_dir) / "server.py"
            server_file.write_text(script)

            transport: PythonStdioTransport = PythonStdioTransport(
                script_path=server_file
            )

            async with Client[PythonStdioTransport](transport=transport) as client:
                tools = await client.list_tools()

                assert tools == []

        settings = Settings(
            server_auth="fastmcp.server.auth.providers.azure.AzureProvider"
        )

        auth_class = settings.server_auth_class

        assert auth_class is AzureProvider


class TestAbstractCollectionTypes:
    """Test that FastMCP accepts abstract collection types from collections.abc."""

    async def test_fastmcp_init_with_tuples(self):
        """Test FastMCP accepts tuples for sequence parameters."""

        def dummy_tool() -> str:
            return "test"

        # Test with tuples and other abstract types
        mcp = FastMCP(
            "test",
            middleware=(),  # Empty tuple
            tools=(Tool.from_function(dummy_tool),),  # Tuple of tools
            include_tags={"tag1", "tag2"},  # Set
            exclude_tags=frozenset({"tag3"}),  # Frozen set
        )
        assert mcp is not None
        assert mcp.name == "test"
        assert isinstance(mcp.middleware, list)  # Should be converted to list

    async def test_fastmcp_init_with_readonly_mapping(self):
        """Test FastMCP accepts read-only mappings."""
        from types import MappingProxyType

        # Test with read-only mapping
        mcp = FastMCP(
            "test2",
            tool_transformations=MappingProxyType({}),  # Read-only mapping
        )
        assert mcp is not None

    async def test_fastmcp_works_with_abstract_types(self):
        """Test that abstract types work end-to-end with a client."""

        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Create server with tuple of tools
        mcp = FastMCP("test", tools=(Tool.from_function(greet),))

        # Verify it works with a client
        async with Client(mcp) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "Hello, World!"
