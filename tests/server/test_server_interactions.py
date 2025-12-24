import base64
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp import McpError
from mcp.types import (
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel
from typing_extensions import TypedDict

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from fastmcp.prompts.prompt import PromptMessage, PromptResult
from fastmcp.resources import FileResource
from fastmcp.resources.resource import FunctionResource
from fastmcp.utilities.tests import temporary_settings
from fastmcp.utilities.types import Audio, File, Image


def _normalize_anyof_order(schema):
    """Normalize the order of items in anyOf arrays for consistent comparison."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            # Sort anyOf items by their string representation for consistent ordering
            schema = schema.copy()
            schema["anyOf"] = sorted(schema["anyOf"], key=str)
        # Recursively normalize nested objects
        return {k: _normalize_anyof_order(v) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [_normalize_anyof_order(item) for item in schema]
    return schema


class PersonTypedDict(TypedDict):
    name: str
    age: int


class PersonModel(BaseModel):
    name: str
    age: int


@dataclass
class PersonDataclass:
    name: str
    age: int


@pytest.fixture
def tool_server():
    mcp = FastMCP()

    @mcp.tool
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool
    def list_tool() -> list[str | int]:
        return ["x", 2]

    @mcp.tool
    def error_tool() -> None:
        raise ValueError("Test error")

    @mcp.tool
    def image_tool(path: str) -> Image:
        return Image(path)

    @mcp.tool
    def audio_tool(path: str) -> Audio:
        return Audio(path)

    @mcp.tool
    def file_tool(path: str) -> File:
        return File(path)

    @mcp.tool
    def mixed_content_tool() -> list[TextContent | ImageContent | EmbeddedResource]:
        return [
            TextContent(type="text", text="Hello"),
            ImageContent(type="image", data="abc", mimeType="application/octet-stream"),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    blob=base64.b64encode(b"abc").decode(),
                    mimeType="application/octet-stream",
                    uri=AnyUrl("file:///test.bin"),
                ),
            ),
        ]

    @mcp.tool(output_schema=None)
    def mixed_list_fn(image_path: str) -> list:
        return [
            "text message",
            Image(image_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_audio_list_fn(audio_path: str) -> list:
        return [
            "text message",
            Audio(audio_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool(output_schema=None)
    def mixed_file_list_fn(file_path: str) -> list:
        return [
            "text message",
            File(file_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    @mcp.tool
    def file_text_tool() -> File:
        # Return a File with text data and text/plain format
        return File(data=b"hello world", format="plain")

    return mcp


class TestTools:
    async def test_add_tool_exists(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            tools = await client.list_tools()
            assert "add" in [t.name for t in tools]

    async def test_list_tools(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            assert len(await client.list_tools()) == 11

    async def test_call_tool_mcp(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool_mcp("add", {"x": 1, "y": 2})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "3"
            assert result.structuredContent == {"result": 3}

    async def test_call_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "3"
            assert result.structured_content == {"result": 3}
            assert result.data == 3

    async def test_call_tool_error(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            with pytest.raises(Exception):
                await client.call_tool("error_tool", {})

    async def test_call_tool_error_as_client_raw(self):
        """Test raising and catching errors from a tool."""
        mcp = FastMCP()
        client = Client(transport=FastMCPTransport(mcp))

        @mcp.tool
        def error_tool():
            raise ValueError("Test error")

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.call_tool("error_tool", {})
            assert "Error calling tool 'error_tool'" in str(excinfo.value)

    async def test_tool_returns_list(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("list_tool", {})
            # Adjacent non-MCP list items are combined into single content block
            assert len(result.content) == 1
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == '["x",2]'
            assert result.data == ["x", 2]

    async def test_file_text_tool(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("file_text_tool", {})
            assert len(result.content) == 1
            embedded = result.content[0]
            assert isinstance(embedded, EmbeddedResource)
            resource = embedded.resource
            assert isinstance(resource, TextResourceContents)
            assert resource.mimeType == "text/plain"
            assert resource.text == "hello world"


class TestToolTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.tool(tags={"a", "b"})
        def tool_1() -> int:
            return 1

        @mcp.tool(tags={"b", "c"})
        def tool_2() -> int:
            return 2

        return mcp

    async def test_include_tags_all_tools(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_1", "tool_2"}

    async def test_include_tags_some_tools(self):
        mcp = self.create_server(include_tags={"a", "z"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_1"}

    async def test_exclude_tags_all_tools(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == set()

    async def test_exclude_tags_some_tools(self):
        mcp = self.create_server(exclude_tags={"a", "z"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"tool_2"}

    async def test_call_included_tool(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result_1 = await client.call_tool("tool_1", {})
            assert result_1.data == 1

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_2", {})

    async def test_call_excluded_tool(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("tool_1", {})

            result_2 = await client.call_tool("tool_2", {})
            assert result_2.data == 2


class TestToolEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        assert sample_tool.enabled

        tool = await mcp.get_tool("sample_tool")
        assert tool.enabled

        tool.disable()

        assert not tool.enabled
        assert not sample_tool.enabled

        tool.enable()
        assert tool.enabled
        assert sample_tool.enabled

    async def test_tool_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_tool_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        sample_tool.enable()

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 1

    async def test_tool_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        sample_tool.disable()

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_get_tool_and_disable(self):
        mcp = FastMCP()

        @mcp.tool
        def sample_tool(x: int) -> int:
            return x * 2

        tool = await mcp.get_tool("sample_tool")
        assert tool.enabled

        sample_tool.disable()

        async with Client(mcp) as client:
            result = await client.list_tools()
            assert len(result) == 0

            with pytest.raises(ToolError, match="Unknown tool"):
                await client.call_tool("sample_tool", {"x": 5})

    async def test_cant_call_disabled_tool(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def sample_tool(x: int) -> int:
            return x * 2

        with pytest.raises(Exception, match="Unknown tool"):
            async with Client(mcp) as client:
                await client.call_tool("sample_tool", {"x": 5})


class TestResource:
    async def test_text_resource(self):
        mcp = FastMCP()

        def get_text():
            return "Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("resource://test"), name="test", fn=get_text
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello, world!"

    async def test_binary_resource(self):
        mcp = FastMCP()

        def get_binary():
            return b"Binary data"

        resource = FunctionResource(
            uri=AnyUrl("resource://binary"),
            name="binary",
            fn=get_binary,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://binary"))
            assert isinstance(result[0], BlobResourceContents)
            assert result[0].blob == base64.b64encode(b"Binary data").decode()

    async def test_file_resource_text(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a text file
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello from file!")

        resource = FileResource(
            uri=AnyUrl("file://test.txt"), name="test.txt", path=text_file
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("file://test.txt"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello from file!"

    async def test_file_resource_binary(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a binary file
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"Binary file data")

        resource = FileResource(
            uri=AnyUrl("file://test.bin"),
            name="test.bin",
            path=binary_file,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("file://test.bin"))
            assert isinstance(result[0], BlobResourceContents)
            assert result[0].blob == base64.b64encode(b"Binary file data").decode()

    async def test_resource_with_annotations(self):
        mcp = FastMCP()

        @mcp.resource(
            "http://example.com/data",
            name="test",
            annotations={
                "httpMethod": "GET",
                "Cache-Control": "max-age=3600",
            },
        )
        def get_data() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1

            resource = resources[0]
            assert str(resource.uri) == "http://example.com/data"

            assert resource.annotations is not None
            assert hasattr(resource.annotations, "httpMethod")
            assert getattr(resource.annotations, "httpMethod") == "GET"
            assert hasattr(resource.annotations, "Cache-Control")
            assert getattr(resource.annotations, "Cache-Control") == "max-age=3600"


class TestResourceTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.resource("resource://1", tags={"a", "b"})
        def resource_1() -> str:
            return "1"

        @mcp.resource("resource://2", tags={"b", "c"})
        def resource_2() -> str:
            return "2"

        return mcp

    async def test_include_tags_all_resources(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_1", "resource_2"}

    async def test_include_tags_some_resources(self):
        mcp = self.create_server(include_tags={"a", "z"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_1"}

    async def test_exclude_tags_all_resources(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == set()

    async def test_exclude_tags_some_resources(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_2"}

    async def test_exclude_precedence(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert {r.name for r in resources} == {"resource_2"}

    async def test_read_included_resource(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://1"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "1"

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://2"))

    async def test_read_excluded_resource(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://1"))


class TestResourceEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        assert sample_resource.enabled

        resource = await mcp.get_resource("resource://data")
        assert resource.enabled

        resource.disable()

        assert not resource.enabled
        assert not sample_resource.enabled

        resource.enable()
        assert resource.enabled
        assert sample_resource.enabled

    async def test_resource_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_resource_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        sample_resource.enable()

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 1

    async def test_resource_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        sample_resource.disable()

        async with Client(mcp) as client:
            resources = await client.list_resources()
            assert len(resources) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_get_resource_and_disable(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def sample_resource() -> str:
            return "Hello, world!"

        resource = await mcp.get_resource("resource://data")
        assert resource.enabled

        sample_resource.disable()

        async with Client(mcp) as client:
            result = await client.list_resources()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://data"))

    async def test_cant_read_disabled_resource(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", enabled=False)
        def sample_resource() -> str:
            return "Hello, world!"

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource(AnyUrl("resource://data"))


class TestResourceTemplatesTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.resource("resource://1/{param}", tags={"a", "b"})
        def template_resource_1(param: str) -> str:
            return f"Template resource 1: {param}"

        @mcp.resource("resource://2/{param}", tags={"b", "c"})
        def template_resource_2(param: str) -> str:
            return f"Template resource 2: {param}"

        return mcp

    async def test_include_tags_all_resources(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {
                "template_resource_1",
                "template_resource_2",
            }

    async def test_include_tags_some_resources(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_1"}

    async def test_exclude_tags_all_resources(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == set()

    async def test_exclude_tags_some_resources(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_2"}

    async def test_exclude_takes_precedence_over_include(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            resources = await client.list_resource_templates()
            assert {r.name for r in resources} == {"template_resource_2"}

    async def test_read_resource_template_includes_tags(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.read_resource("resource://1/x")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 1: x"

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("resource://2/x")

    async def test_read_resource_template_excludes_tags(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource("resource://1/x")

            result = await client.read_resource("resource://2/x")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 2: x"


class TestResourceTemplateEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        assert sample_template.enabled

        template = await mcp.get_resource_template("resource://{param}")
        assert template.enabled

        template.disable()

        assert not template.enabled
        assert not sample_template.enabled

        template.enable()
        assert template.enabled
        assert sample_template.enabled

    async def test_template_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_template_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        sample_template.enable()

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1

    async def test_template_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        sample_template.disable()

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_get_template_and_disable(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        template = await mcp.get_resource_template("resource://{param}")
        assert template.enabled

        sample_template.disable()

        async with Client(mcp) as client:
            result = await client.list_resource_templates()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown resource"):
                await client.read_resource(AnyUrl("resource://test"))

    async def test_cant_read_disabled_template(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", enabled=False)
        def sample_template(param: str) -> str:
            return f"Template: {param}"

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource(AnyUrl("resource://test"))


class TestPrompts:
    """Test prompt functionality in FastMCP server."""

    async def test_prompt_decorator(self):
        """Test that the prompt decorator registers prompts correctly."""
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"
        # Don't compare functions directly since validate_call wraps them
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_name(self):
        """Test prompt decorator with custom name."""
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["custom_name"]
        assert prompt.name == "custom_name"
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_description(self):
        """Test prompt decorator with custom description."""
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.description == "A custom description"
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_parens(self):
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"

    async def test_list_prompts(self):
        """Test listing prompts through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def fn(name: str, optional: str = "default") -> str:
            return f"Hello, {name}! {optional}"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "fn"
            assert prompts[0].description is None
            assert prompts[0].arguments is not None
            assert len(prompts[0].arguments) == 2
            assert prompts[0].arguments[0].name == "name"
            assert prompts[0].arguments[0].required is True
            assert prompts[0].arguments[1].name == "optional"
            assert prompts[0].arguments[1].required is False

    async def test_list_prompts_with_enhanced_descriptions(self):
        """Test that enhanced descriptions with JSON schema are visible via MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def analyze_data(
            name: str, numbers: list[int], metadata: dict[str, str], threshold: float
        ) -> str:
            """Analyze some data."""
            return f"Analyzed {name}"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            prompt = prompts[0]
            assert prompt.name == "analyze_data"
            assert prompt.description == "Analyze some data."

            # Find each argument and verify schema enhancements
            assert prompt.arguments is not None
            args_by_name = {arg.name: arg for arg in prompt.arguments}

            # String parameter should not have schema enhancement
            name_arg = args_by_name["name"]
            assert name_arg.description is None

            # Non-string parameters should have schema enhancements
            numbers_arg = args_by_name["numbers"]
            assert numbers_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in numbers_arg.description
            )
            assert (
                '{"items":{"type":"integer"},"type":"array"}' in numbers_arg.description
            )

            metadata_arg = args_by_name["metadata"]
            assert metadata_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in metadata_arg.description
            )
            assert (
                '{"additionalProperties":{"type":"string"},"type":"object"}'
                in metadata_arg.description
            )

            threshold_arg = args_by_name["threshold"]
            assert threshold_arg.description is not None
            assert (
                "Provide as a JSON string matching the following schema:"
                in threshold_arg.description
            )
            assert '{"type":"number"}' in threshold_arg.description

    async def test_get_prompt(self):
        """Test getting a prompt through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt
        def fn(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            result = await client.get_prompt("fn", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            content = message.content
            assert isinstance(content, TextContent)
            assert content.text == "Hello, World!"

    async def test_get_prompt_with_resource(self):
        """Test getting a prompt that returns resource content."""
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        async with Client(mcp) as client:
            result = await client.get_prompt("fn")
            assert result.messages[0].role == "user"
            content = result.messages[0].content
            assert isinstance(content, EmbeddedResource)
            assert isinstance(content.resource, TextResourceContents)
            assert content.resource.text == "File contents"
            assert content.resource.mimeType == "text/plain"

    async def test_get_unknown_prompt(self):
        """Test error when getting unknown prompt."""
        mcp = FastMCP()
        with pytest.raises(McpError, match="Unknown prompt"):
            async with Client(mcp) as client:
                await client.get_prompt("unknown")

    async def test_get_prompt_missing_args(self):
        """Test error when required arguments are missing."""
        mcp = FastMCP()

        @mcp.prompt
        def prompt_fn(name: str) -> str:
            return f"Hello, {name}!"

        with pytest.raises(McpError, match="Missing required arguments"):
            async with Client(mcp) as client:
                await client.get_prompt("prompt_fn")

    async def test_resource_decorator_with_tags(self):
        """Test that the resource decorator supports tags."""
        mcp = FastMCP()

        @mcp.resource("resource://data", tags={"example", "test-tag"})
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"example", "test-tag"}

    async def test_template_decorator_with_tags(self):
        """Test that the template decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_prompt_decorator_with_tags(self):
        """Test that the prompt decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["sample_prompt"]
        assert prompt.tags == {"example", "test-tag"}


class TestPromptEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        assert sample_prompt.enabled

        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt.enabled

        prompt.disable()

        assert not prompt.enabled
        assert not sample_prompt.enabled

        prompt.enable()
        assert prompt.enabled
        assert sample_prompt.enabled

    async def test_prompt_disabled_in_decorator(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_prompt_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        sample_prompt.enable()

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 1

    async def test_prompt_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        sample_prompt.disable()

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert len(prompts) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_get_prompt_and_disable(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt.enabled

        sample_prompt.disable()

        async with Client(mcp) as client:
            result = await client.list_prompts()
            assert len(result) == 0

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("sample_prompt")

    async def test_cant_get_disabled_prompt(self):
        mcp = FastMCP()

        @mcp.prompt(enabled=False)
        def sample_prompt() -> str:
            return "Hello, world!"

        with pytest.raises(McpError, match="Unknown prompt"):
            async with Client(mcp) as client:
                await client.get_prompt("sample_prompt")


class TestPromptTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.prompt(tags={"a", "b"})
        def prompt_1() -> str:
            return "1"

        @mcp.prompt(tags={"b", "c"})
        def prompt_2() -> str:
            return "2"

        return mcp

    async def test_include_tags_all_prompts(self):
        mcp = self.create_server(include_tags={"a", "b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_1", "prompt_2"}

    async def test_include_tags_some_prompts(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_1"}

    async def test_exclude_tags_all_prompts(self):
        mcp = self.create_server(exclude_tags={"a", "b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == set()

    async def test_exclude_tags_some_prompts(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_2"}

    async def test_exclude_takes_precedence_over_include(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts} == {"prompt_2"}

    async def test_read_prompt_includes_tags(self):
        mcp = self.create_server(include_tags={"a"})

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_1")
            assert isinstance(result.messages[0].content, TextContent)
            assert result.messages[0].content.text == "1"

            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("prompt_2")

    async def test_read_prompt_excludes_tags(self):
        mcp = self.create_server(exclude_tags={"a"})

        async with Client(mcp) as client:
            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("prompt_1")

            result = await client.get_prompt("prompt_2")
            assert isinstance(result.messages[0].content, TextContent)
            assert result.messages[0].content.text == "2"


class TestMeta:
    """Test that include_fastmcp_meta controls whether _fastmcp key is present in meta."""

    async def test_tool_tags_in_meta_with_default_setting(self):
        """Test that tool tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.tool(tags={"tool-example", "test-tool-tag"})
        def sample_tool(x: int) -> int:
            """A sample tool."""
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "sample_tool")
            assert tool.meta is not None
            assert set(tool.meta["_fastmcp"]["tags"]) == {
                "tool-example",
                "test-tool-tag",
            }

    async def test_resource_tags_in_meta_with_default_setting(self):
        """Test that resource tags appear in meta under _fastmcp key with default setting."""
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
            assert set(resource.meta["_fastmcp"]["tags"]) == {
                "resource-example",
                "test-resource-tag",
            }

    async def test_resource_template_tags_in_meta_with_default_setting(self):
        """Test that resource template tags appear in meta under _fastmcp key with default setting."""
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
            assert set(template.meta["_fastmcp"]["tags"]) == {
                "template-example",
                "test-template-tag",
            }

    async def test_prompt_tags_in_meta_with_default_setting(self):
        """Test that prompt tags appear in meta under _fastmcp key with default setting."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt = next(p for p in prompts if p.name == "sample_prompt")
            assert prompt.meta is not None
            assert set(prompt.meta["_fastmcp"]["tags"]) == {"example", "test-tag"}

    async def test_tool_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.tool(tags={"tool-example", "test-tool-tag"})
        def sample_tool(x: int) -> int:
            """A sample tool."""
            return x * 2

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "sample_tool")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert tool.meta is None

    async def test_resource_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.resource(
            uri="test://resource", tags={"resource-example", "test-resource-tag"}
        )
        def sample_resource() -> str:
            """A sample resource."""
            return "resource content"

        async with Client(mcp) as client:
            resources = await client.list_resources()
            resource = next(r for r in resources if str(r.uri) == "test://resource")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert resource.meta is None

    async def test_resource_template_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

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
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert template.meta is None

    async def test_prompt_meta_with_include_fastmcp_meta_false(self):
        mcp = FastMCP(include_fastmcp_meta=False)

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            prompt = next(p for p in prompts if p.name == "sample_prompt")
            # Meta should be None when include_fastmcp_meta is False and no explicit meta is set
            assert prompt.meta is None

    async def test_global_settings_inheritance(self):
        """Test that servers inherit the global include_fastmcp_meta setting."""
        with temporary_settings(include_fastmcp_meta=False):
            # Server should inherit global setting
            mcp = FastMCP()

            @mcp.tool(tags={"test-tag"})
            def sample_tool(x: int) -> int:
                return x * 2

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool = next(t for t in tools if t.name == "sample_tool")
                # Meta should be None because global setting is False
                assert tool.meta is None

        # Verify that default behavior is restored
        mcp2 = FastMCP()

        @mcp2.tool(tags={"test-tag"})
        def another_tool(x: int) -> int:
            return x * 2

        async with Client(mcp2) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "another_tool")
            # Meta should have _fastmcp key because global setting is back to default (True)
            assert tool.meta is not None
            assert "_fastmcp" in tool.meta
            assert tool.meta["_fastmcp"]["tags"] == ["test-tag"]

    async def test_explicit_override_of_global_setting(self):
        """Test that explicit include_fastmcp_meta parameter overrides global setting."""
        with temporary_settings(include_fastmcp_meta=False):
            # Explicitly override global setting to True
            mcp = FastMCP(include_fastmcp_meta=True)

            @mcp.tool(tags={"test-tag"})
            def sample_tool(x: int) -> int:
                return x * 2

            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool = next(t for t in tools if t.name == "sample_tool")
                # Meta should have _fastmcp key because explicit setting overrides global
                assert tool.meta is not None
                assert "_fastmcp" in tool.meta
                assert tool.meta["_fastmcp"]["tags"] == ["test-tag"]
