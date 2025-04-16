import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from mcp.types import (
    BlobResourceContents,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, Field

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ClientError, NotFoundError, ToolError
from fastmcp.prompts.prompt import EmbeddedResource, Message, UserMessage
from fastmcp.resources import FileResource, FunctionResource
from fastmcp.utilities.types import Image

if TYPE_CHECKING:
    from fastmcp import Context


class TestCreateServer:
    async def test_create_server(self):
        mcp = FastMCP(instructions="Server instructions")
        assert mcp.name == "FastMCP"
        assert mcp.instructions == "Server instructions"

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
            assert len(result) == 1
            content = result[0]
            assert isinstance(content, TextContent)
            assert "¡Hola, 世界! 👋" == content.text


class TestTools:
    async def test_mcp_tool_name(self):
        """Test MCPTool name for add_tool (key != tool.name)."""

        mcp = FastMCP()

        @mcp.tool()
        def fn(x: int) -> int:
            return x + 1

        mcp_tools = await mcp._mcp_list_tools()
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "fn"

    async def test_mcp_tool_custom_name(self):
        """Test MCPTool name for add_tool (key != tool.name)."""

        mcp = FastMCP()

        @mcp.tool(name="custom_name")
        def fn(x: int) -> int:
            return x + 1

        mcp_tools = await mcp._mcp_list_tools()
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "custom_name"


class TestToolDecorator:
    async def test_no_tools_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(NotFoundError, match="Unknown tool: add"):
            await mcp._mcp_call_tool("add", {"x": 1, "y": 2})

    async def test_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool()
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_tool_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(TypeError, match="The @tool decorator was used incorrectly"):

            @mcp.tool  # Missing parentheses #type: ignore
            def add(x: int, y: int) -> int:
                return x + y

    async def test_tool_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.tool(name="custom-add")
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp._mcp_call_tool("custom-add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_tool_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.tool(description="Add two numbers")
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp._mcp_list_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.description == "Add two numbers"

    async def test_tool_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, x: int):
                self.x = x

            @mcp.tool()
            def add(self, y: int) -> int:
                return self.x + y

        obj = MyClass(10)
        mcp.add_tool(obj.add)
        result = await mcp._mcp_call_tool("add", {"y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "12"

    async def test_tool_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            x: int = 10

            @classmethod
            def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "12"

    async def test_tool_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.tool()
            def add(x: int, y: int) -> int:
                return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_tool_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.tool()
        async def add(x: int, y: int) -> int:
            return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_tool_decorator_classmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            x = 10

            @classmethod
            async def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "12"

    async def test_tool_decorator_staticmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            async def add(x: int, y: int) -> int:
                return x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        # Verify the tags were set correctly
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 1
        assert tools[0].tags == {"example", "test-tag"}

    async def test_add_tool_with_custom_name(self):
        """Test adding a tool with a custom name using server.add_tool()."""
        mcp = FastMCP()

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        mcp.add_tool(multiply, name="custom_multiply")

        # Check that the tool is registered with the custom name
        tools = await mcp.get_tools()
        assert "custom_multiply" in tools

        # Call the tool by its custom name
        result = await mcp._mcp_call_tool("custom_multiply", {"a": 5, "b": 3})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "15"

        # Original name should not be registered
        assert "multiply" not in tools


class TestResourceDecorator:
    async def test_no_resources_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(ClientError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource("resource://data")

    async def test_resource_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def get_data() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello, world!"

    async def test_resource_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(
            TypeError, match="The @resource decorator was used incorrectly"
        ):

            @mcp.resource  # Missing parentheses #type: ignore
            def get_data() -> str:
                return "Hello, world!"

    async def test_resource_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", name="custom-data")
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].name == "custom-data"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello, world!"

    async def test_resource_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", description="Data resource")
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].description == "Data resource"

    async def test_resource_decorator_with_tags(self):
        """Test that the resource decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://data", tags={"example", "test-tag"})
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"example", "test-tag"}

    async def test_resource_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def get_data(self) -> str:
                return f"{self.prefix} Hello, world!"

        obj = MyClass("My prefix:")
        mcp.add_resource_fn(
            obj.get_data, uri="resource://data", name="instance-resource"
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "My prefix: Hello, world!"

    async def test_resource_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_resource_fn(
            MyClass.get_data, uri="resource://data", name="class-resource"
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Class prefix: Hello, world!"

    async def test_resource_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.resource("resource://data")
            def get_data() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static Hello, world!"

    async def test_resource_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        async def get_data() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Async Hello, world!"


class TestTemplateDecorator:
    async def test_template_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "get_data"
        assert templates[0].uri_template == "resource://{name}/data"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for test"

    async def test_template_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(
            TypeError, match="The @resource decorator was used incorrectly"
        ):

            @mcp.resource  # Missing parentheses #type: ignore
            def get_data(name: str) -> str:
                return f"Data for {name}"

    async def test_template_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data", name="custom-template")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "custom-template"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Data for test"

    async def test_template_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data", description="Template description")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].description == "Template description"

    async def test_template_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def get_data(self, name: str) -> str:
                return f"{self.prefix} Data for {name}"

        obj = MyClass("My prefix:")
        mcp.add_resource_fn(
            obj.get_data, uri="resource://{name}/data", name="instance-template"
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "My prefix: Data for test"

    async def test_template_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls, name: str) -> str:
                return f"{cls.prefix} Data for {name}"

        mcp.add_resource_fn(
            MyClass.get_data,
            uri="resource://{name}/data",
            name="class-template",
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Class prefix: Data for test"

    async def test_template_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.resource("resource://{name}/data")
            def get_data(name: str) -> str:
                return f"Static Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static Data for test"

    async def test_template_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        async def get_data(name: str) -> str:
            return f"Async Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Async Data for test"

    async def test_template_decorator_with_tags(self):
        """Test that the template decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}


class TestPromptDecorator:
    async def test_prompt_decorator(self):
        mcp = FastMCP()

        @mcp.prompt()
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"
        # Don't compare functions directly since validate_call wraps them
        content = await prompt.render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    async def test_prompt_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(
            TypeError, match="The @prompt decorator was used incorrectly"
        ):

            @mcp.prompt  # Missing parentheses #type: ignore
            def fn() -> str:
                return "Hello, world!"

    async def test_prompt_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["custom_name"]
        assert prompt.name == "custom_name"
        content = await prompt.render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.description == "A custom description"
        content = await prompt.render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_parameters(self):
        mcp = FastMCP()

        @mcp.prompt()
        def test_prompt(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["test_prompt"]
        assert prompt.arguments is not None
        assert len(prompt.arguments) == 2
        assert prompt.arguments[0].name == "name"
        assert prompt.arguments[0].required is True
        assert prompt.arguments[1].name == "greeting"
        assert prompt.arguments[1].required is False

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt", {"name": "World"})
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Hello, World!"

            result = await client.get_prompt(
                "test_prompt", {"name": "World", "greeting": "Hi"}
            )
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Hi, World!"

    async def test_prompt_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def test_prompt(self) -> str:
                return f"{self.prefix} Hello, world!"

        obj = MyClass("My prefix:")
        mcp.add_prompt(obj.test_prompt, name="test_prompt")

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "My prefix: Hello, world!"

    async def test_prompt_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def test_prompt(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_prompt(MyClass.test_prompt, name="test_prompt")

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Class prefix: Hello, world!"

    async def test_prompt_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.prompt()
            def test_prompt() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Static Hello, world!"

    async def test_prompt_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.prompt()
        async def test_prompt() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result) == 1
            message = result[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Async Hello, world!"

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


@pytest.fixture
def tool_server():
    mcp = FastMCP()

    @mcp.tool()
    def add(x: int, y: int) -> int:
        return x + y

    @mcp.tool()
    def list_tool() -> list[str | int]:
        return ["x", 2]

    @mcp.tool()
    def error_tool() -> None:
        raise ValueError("Test error")

    @mcp.tool()
    def image_tool(path: str) -> Image:
        return Image(path)

    @mcp.tool()
    def mixed_content_tool() -> list[TextContent | ImageContent]:
        return [
            TextContent(type="text", text="Hello"),
            ImageContent(type="image", data="abc", mimeType="image/png"),
        ]

    @mcp.tool()
    def mixed_list_fn(image_path: str) -> list:
        return [
            "text message",
            Image(image_path),
            {"key": "value"},
            TextContent(type="text", text="direct content"),
        ]

    return mcp


class TestServerTools:
    async def test_add_tool_exists(self, tool_server: FastMCP):
        assert "add" in [t.name for t in await tool_server._mcp_list_tools()]

    async def test_list_tools(self, tool_server: FastMCP):
        assert len(await tool_server._mcp_list_tools()) == 6

    async def test_call_tool(self, tool_server: FastMCP):
        result = await tool_server._mcp_call_tool("add", {"x": 1, "y": 2})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"

    async def test_call_tool_as_client(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert isinstance(result[0], TextContent)
            assert result[0].text == "3"

    async def test_call_tool_error(self, tool_server: FastMCP):
        with pytest.raises(ToolError):
            await tool_server._mcp_call_tool("error_tool", {})

    async def test_call_tool_error_as_client(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            with pytest.raises(Exception):
                await client.call_tool("error_tool", {})

    async def test_call_tool_error_as_client_raw(self, tool_server: FastMCP):
        async with Client(tool_server) as client:
            result = await client.call_tool("error_tool", {}, _return_raw_result=True)
        assert result.isError
        assert isinstance(result.content[0], TextContent)
        assert "Test error" in result.content[0].text

    async def test_tool_returns_list(self, tool_server: FastMCP):
        result = await tool_server._mcp_call_tool("list_tool", {})
        assert isinstance(result[0], TextContent)
        assert result[0].text == '["x", 2]'

    async def test_tool_image_helper(self, tool_server: FastMCP, tmp_path: Path):
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"fake png data")

        result = await tool_server._mcp_call_tool(
            "image_tool", {"path": str(image_path)}
        )
        content = result[0]
        assert isinstance(content, ImageContent)
        assert content.type == "image"
        assert content.mimeType == "image/png"
        # Verify base64 encoding
        decoded = base64.b64decode(content.data)
        assert decoded == b"fake png data"

    async def test_tool_mixed_content(self, tool_server: FastMCP):
        result = await tool_server._mcp_call_tool("mixed_content_tool", {})
        assert len(result) == 2
        content1 = result[0]
        content2 = result[1]
        assert isinstance(content1, TextContent)
        assert content1.text == "Hello"
        assert isinstance(content2, ImageContent)
        assert content2.mimeType == "image/png"
        assert content2.data == "abc"

    async def test_tool_mixed_list_with_image(
        self, tool_server: FastMCP, tmp_path: Path
    ):
        """Test that lists containing Image objects and other types are handled
        correctly. Note that the non-MCP content will be grouped together."""
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"test image data")

        result = await tool_server._mcp_call_tool(
            "mixed_list_fn", {"image_path": str(image_path)}
        )
        assert len(result) == 3
        # Check text conversion
        content1 = result[0]
        assert isinstance(content1, TextContent)
        assert json.loads(content1.text) == ["text message", {"key": "value"}]
        # Check image conversion
        content2 = result[1]
        assert isinstance(content2, ImageContent)
        assert content2.mimeType == "image/png"
        assert base64.b64decode(content2.data) == b"test image data"
        # Check direct TextContent
        content3 = result[2]
        assert isinstance(content3, TextContent)
        assert content3.text == "direct content"

    async def test_parameter_descriptions(self):
        mcp = FastMCP("Test Server")

        @mcp.tool()
        def greet(
            name: str = Field(description="The name to greet"),
            title: str = Field(description="Optional title", default=""),
        ) -> str:
            """A greeting tool"""
            return f"Hello {title} {name}"

        tools = await mcp._mcp_list_tools()
        assert len(tools) == 1
        tool = tools[0]

        # Check that parameter descriptions are present in the schema
        properties = tool.inputSchema["properties"]
        assert "name" in properties
        assert properties["name"]["description"] == "The name to greet"
        assert "title" in properties
        assert properties["title"]["description"] == "Optional title"


class TestServerResources:
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


class TestServerResourceTemplates:
    async def test_resource_with_params_not_in_uri(self):
        """Test that a resource with function parameters raises an error if the URI
        parameters don't match"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI template must contain at least one parameter",
        ):

            @mcp.resource("resource://data")
            def get_data_fn(param: str) -> str:
                return f"Data: {param}"

    async def test_resource_with_uri_params_without_args(self):
        """Test that a resource with URI parameters is automatically a template"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI parameters .* must be a subset of the function arguments",
        ):

            @mcp.resource("resource://{param}")
            def get_data() -> str:
                return "Data"

    async def test_resource_with_untyped_params(self):
        """Test that a resource with untyped parameters raises an error"""
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def get_data(param) -> str:
            return "Data"

    async def test_resource_matching_params(self):
        """Test that a resource with matching URI and function parameters works"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for test"

    async def test_resource_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI parameters .* must be a subset of the required function arguments",
        ):

            @mcp.resource("resource://{name}/data")
            def get_data(user: str) -> str:
                return f"Data for {user}"

    async def test_resource_multiple_params(self):
        """Test that multiple parameters work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{org}/{repo}/data")
        def get_data(org: str, repo: str) -> str:
            return f"Data for {org}/{repo}"

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("resource://cursor/fastmcp/data")
            )
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for cursor/fastmcp"

    async def test_resource_multiple_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI parameters .* must be a subset of the required function arguments",
        ):

            @mcp.resource("resource://{org}/{repo}/data")
            def get_data_mismatched(org: str, repo_2: str) -> str:
                return f"Data for {org}"

        """Test that a resource with no parameters works as a regular resource"""
        mcp = FastMCP()

        @mcp.resource("resource://static")
        def get_static_data() -> str:
            return "Static data"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://static"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static data"

    async def test_template_with_default_params(self):
        """Test that a template can have default parameters."""
        mcp = FastMCP()

        @mcp.resource("math://add/{x}")
        def add(x: int, y: int = 10) -> int:
            return x + y

        # Verify it's registered as a template
        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "math://add/{x}"

        # Call the template and verify it uses the default value
        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("math://add/5"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "15"  # 5 + default 10

        # Can also call with explicit params
        resource = await mcp._resource_manager.get_resource("math://add/7")
        assert isinstance(resource, FunctionResource)
        result = await resource.read()
        assert result == "17"  # 7 + default 10

    async def test_template_to_resource_conversion(self):
        """Test that a template can be converted to a resource."""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        # Verify it's registered as a template
        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "resource://{name}/data"

        # When accessed, should create a concrete resource
        resource = await mcp._resource_manager.get_resource("resource://test/data")
        assert isinstance(resource, FunctionResource)
        result = await resource.read()
        assert result == "Data for test"

    async def test_stacked_resource_template_decorators(self):
        """Test that resource template decorators can be stacked."""
        mcp = FastMCP()

        @mcp.resource("users://email/{email}")
        @mcp.resource("users://name/{name}")
        def lookup_user(name: str | None = None, email: str | None = None) -> dict:
            if name:
                return {
                    "id": "123",
                    "name": name,
                    "email": "dummy@example.com",
                    "lookup": "name",
                }
            elif email:
                return {
                    "id": "123",
                    "name": "Test User",
                    "email": email,
                    "lookup": "email",
                }
            else:
                raise ValueError("Either name or email must be provided")

        # Verify both templates are registered
        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 2
        template_uris = {t.uri_template for t in templates}
        assert "users://email/{email}" in template_uris
        assert "users://name/{name}" in template_uris

        # Test lookup by email
        async with Client(mcp) as client:
            email_result = await client.read_resource(
                AnyUrl("users://email/user@example.com")
            )
            assert isinstance(email_result[0], TextResourceContents)
            email_data = json.loads(email_result[0].text)
            assert email_data["lookup"] == "email"
            assert email_data["email"] == "user@example.com"

            # Test lookup by name
            name_result = await client.read_resource(AnyUrl("users://name/John"))
            assert isinstance(name_result[0], TextResourceContents)
            name_data = json.loads(name_result[0].text)
            assert name_data["lookup"] == "name"
            assert name_data["name"] == "John"
            assert name_data["email"] == "dummy@example.com"

    async def test_template_decorator_with_tags(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}


class TestContextInjection:
    """Test context injection in tools."""

    async def test_context_detection(self):
        """Test that context parameters are properly detected."""
        mcp = FastMCP()

        def tool_with_context(x: int, ctx: Context) -> str:
            return f"Request {ctx.request_id}: {x}"

        tool = mcp._tool_manager.add_tool_from_fn(tool_with_context)
        assert tool.context_kwarg == "ctx"

    async def test_context_injection(self):
        """Test that context is properly injected into tool calls."""
        mcp = FastMCP()

        def tool_with_context(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Request {ctx.request_id}: {x}"

        mcp.add_tool(tool_with_context)
        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_context", {"x": 42})
            assert len(result) == 1
            content = result[0]
            assert isinstance(content, TextContent)
            assert "Request" in content.text
            assert "42" in content.text

    async def test_async_context(self):
        """Test that context works in async functions."""
        mcp = FastMCP()

        async def async_tool(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Async request {ctx.request_id}: {x}"

        mcp.add_tool(async_tool)
        async with Client(mcp) as client:
            result = await client.call_tool("async_tool", {"x": 42})
            assert len(result) == 1
            content = result[0]
            assert isinstance(content, TextContent)
            assert "Async request" in content.text
            assert "42" in content.text

    async def test_context_logging(self):
        from unittest.mock import patch

        import mcp.server.session

        """Test that context logging methods work."""
        mcp = FastMCP()

        async def logging_tool(msg: str, ctx: Context) -> str:
            await ctx.debug("Debug message")
            await ctx.info("Info message")
            await ctx.warning("Warning message")
            await ctx.error("Error message")
            return f"Logged messages for {msg}"

        mcp.add_tool(logging_tool)

        with patch("mcp.server.session.ServerSession.send_log_message") as mock_log:
            async with Client(mcp) as client:
                result = await client.call_tool("logging_tool", {"msg": "test"})
                assert len(result) == 1
                content = result[0]
                assert isinstance(content, TextContent)
                assert "Logged messages for test" in content.text

                assert mock_log.call_count == 4
                mock_log.assert_any_call(
                    level="debug", data="Debug message", logger=None
                )
                mock_log.assert_any_call(level="info", data="Info message", logger=None)
                mock_log.assert_any_call(
                    level="warning", data="Warning message", logger=None
                )
                mock_log.assert_any_call(
                    level="error", data="Error message", logger=None
                )

    async def test_optional_context(self):
        """Test that context is optional."""
        mcp = FastMCP()

        def no_context(x: int) -> int:
            return x * 2

        mcp.add_tool(no_context)
        async with Client(mcp) as client:
            result = await client.call_tool("no_context", {"x": 21})
            assert len(result) == 1
            content = result[0]
            assert isinstance(content, TextContent)
            assert content.text == "42"

    async def test_context_resource_access(self):
        """Test that context can access resources."""
        mcp = FastMCP()

        @mcp.resource("test://data")
        def test_resource() -> str:
            return "resource data"

        @mcp.tool()
        async def tool_with_resource(ctx: Context) -> str:
            r_iter = await ctx.read_resource("test://data")
            r_list = list(r_iter)
            assert len(r_list) == 1
            r = r_list[0]
            return f"Read resource: {r.content} with mime type {r.mime_type}"

        async with Client(mcp) as client:
            result = await client.call_tool("tool_with_resource", {})
            assert len(result) == 1
            content = result[0]
            assert isinstance(content, TextContent)
            assert "Read resource: resource data" in content.text


class TestServerPrompts:
    """Test prompt functionality in FastMCP server."""

    async def test_prompt_decorator(self):
        """Test that the prompt decorator registers prompts correctly."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"
        # Don't compare functions directly since validate_call wraps them
        content = await prompt.render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

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
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

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
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    def test_prompt_decorator_error(self):
        """Test error when decorator is used incorrectly."""
        mcp = FastMCP()
        with pytest.raises(TypeError, match="decorator was used incorrectly"):

            @mcp.prompt  # type: ignore
            def fn() -> str:
                return "Hello, world!"

    async def test_list_prompts(self):
        """Test listing prompts through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt()
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

    async def test_get_prompt(self):
        """Test getting a prompt through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn(name: str) -> str:
            return f"Hello, {name}!"

        async with Client(mcp) as client:
            result = await client.get_prompt("fn", {"name": "World"})
            assert len(result) == 1
            message = result[0]
            assert message.role == "user"
            content = message.content
            assert isinstance(content, TextContent)
            assert content.text == "Hello, World!"

    async def test_get_prompt_with_resource(self):
        """Test getting a prompt that returns resource content."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn() -> Message:
            return UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            )

        async with Client(mcp) as client:
            result = await client.get_prompt("fn")
            assert result[0].role == "user"
            content = result[0].content
            assert isinstance(content, EmbeddedResource)
            resource = content.resource
            assert isinstance(resource, TextResourceContents)
            assert resource.text == "File contents"
            assert resource.mimeType == "text/plain"

    async def test_get_unknown_prompt(self):
        """Test error when getting unknown prompt."""
        mcp = FastMCP()
        with pytest.raises(ClientError, match="Unknown prompt"):
            async with Client(mcp) as client:
                await client.get_prompt("unknown")

    async def test_get_prompt_missing_args(self):
        """Test error when required arguments are missing."""
        mcp = FastMCP()

        @mcp.prompt()
        def prompt_fn(name: str) -> str:
            return f"Hello, {name}!"

        with pytest.raises(ClientError, match="Missing required arguments"):
            async with Client(mcp) as client:
                await client.get_prompt("prompt_fn")

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        # Verify the tags were set correctly
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 1
        assert tools[0].tags == {"example", "test-tag"}

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
