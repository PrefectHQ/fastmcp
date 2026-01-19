import os
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest import mock

from mcp.types import TextContent, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.tools import FunctionTool
from fastmcp.tools.tool import Tool
from fastmcp.utilities.tests import temporary_settings


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

        tools = await mcp.list_tools()
        assert any(t.name == "local_tool" for t in tools)


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
        resources = await main_server.list_resources()
        templates = await main_server.list_resource_templates()

        assert any(str(r.uri) == "resource://prefix/test-resource" for r in resources)
        assert any(str(r.uri) == "resource://prefix//absolute/path" for r in resources)
        assert any(
            t.uri_template == "resource://prefix/{param}/template" for t in templates
        )

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


class TestSettingsFromEnvironment:
    async def test_server_starts_without_auth(self):
        """Test that server starts without auth configured."""
        from fastmcp.client.transports import PythonStdioTransport

        script = dedent("""
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
            exclude_tags={"tag3"},  # Set
        )
        assert mcp is not None
        assert mcp.name == "test"
        assert isinstance(mcp.middleware, list)  # Should be converted to list

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


class TestMeta:
    """Test that fastmcp key is always present in meta."""

    async def test_tool_tags_in_meta(self):
        """Test that tool tags appear in meta under fastmcp key."""
        mcp = FastMCP()

        @mcp.tool(tags={"tool-example", "test-tool-tag"})
        def sample_tool(x: int) -> int:
            """A sample tool."""
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "sample_tool")
            assert tool.meta is not None
            assert set(tool.meta["fastmcp"]["tags"]) == {
                "tool-example",
                "test-tool-tag",
            }

    async def test_resource_tags_in_meta(self):
        """Test that resource tags appear in meta under fastmcp key."""
        mcp = FastMCP()

        @mcp.resource(
            uri="test://resource", tags={"resource-example", "test-resource-tag"}
        )
        def sample_resource() -> str:
            """A sample resource."""
            return "resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            resource = next(r for r in resources if str(r.uri) == "test://resource")
            assert resource.meta is not None
            assert set(resource.meta["fastmcp"]["tags"]) == {
                "resource-example",
                "test-resource-tag",
            }

    async def test_resource_template_tags_in_meta(self):
        """Test that resource template tags appear in meta under fastmcp key."""
        mcp = FastMCP()

        @mcp.resource(
            "test://template/{id}", tags={"template-example", "test-template-tag"}
        )
        def sample_template(id: str) -> str:
            """A sample resource template."""
            return f"template content for {id}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            template = next(
                t for t in templates if t.uriTemplate == "test://template/{id}"
            )
            assert template.meta is not None
            assert set(template.meta["fastmcp"]["tags"]) == {
                "template-example",
                "test-template-tag",
            }

    async def test_prompt_tags_in_meta(self):
        """Test that prompt tags appear in meta under fastmcp key."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt = next(p for p in prompts if p.name == "sample_prompt")
            assert prompt.meta is not None
            assert set(prompt.meta["fastmcp"]["tags"]) == {"example", "test-tag"}


class TestShowServerBannerSetting:
    """Test that show_server_banner setting controls banner display."""

    async def test_show_banner_defaults_to_setting_true(self):
        """Test that show_banner=None uses the setting (default True)."""
        mcp = FastMCP()

        with mock.patch.object(mcp, "run_stdio_async") as mock_run:
            mock_run.return_value = None
            await mcp.run_async(transport="stdio")
            mock_run.assert_called_once()
            assert mock_run.call_args.kwargs["show_banner"] is True

    async def test_show_banner_respects_setting_false(self):
        """Test that show_banner=None uses the setting when False."""
        mcp = FastMCP()

        with mock.patch.dict(os.environ, {"FASTMCP_SHOW_SERVER_BANNER": "false"}):
            with temporary_settings(show_server_banner=False):
                with mock.patch.object(mcp, "run_stdio_async") as mock_run:
                    mock_run.return_value = None
                    await mcp.run_async(transport="stdio")
                    mock_run.assert_called_once()
                    assert mock_run.call_args.kwargs["show_banner"] is False

    async def test_show_banner_explicit_true_overrides_setting(self):
        """Test that explicit show_banner=True overrides False setting."""
        mcp = FastMCP()

        with temporary_settings(show_server_banner=False):
            with mock.patch.object(mcp, "run_stdio_async") as mock_run:
                mock_run.return_value = None
                await mcp.run_async(transport="stdio", show_banner=True)
                mock_run.assert_called_once()
                assert mock_run.call_args.kwargs["show_banner"] is True

    async def test_show_banner_explicit_false_overrides_setting(self):
        """Test that explicit show_banner=False overrides True setting."""
        mcp = FastMCP()

        with temporary_settings(show_server_banner=True):
            with mock.patch.object(mcp, "run_stdio_async") as mock_run:
                mock_run.return_value = None
                await mcp.run_async(transport="stdio", show_banner=False)
                mock_run.assert_called_once()
                assert mock_run.call_args.kwargs["show_banner"] is False
