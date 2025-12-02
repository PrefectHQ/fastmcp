from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from typing import Annotated

import pytest
from mcp import McpError
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.prompts.prompt import FunctionPrompt, Prompt
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.server import (
    add_resource_prefix,
    has_resource_prefix,
    remove_resource_prefix,
)
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


class TestToolDecorator:
    async def test_no_tools_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(NotFoundError, match="Unknown tool: add"):
            await mcp._call_tool_mcp("add", {"x": 1, "y": 2})

    async def test_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        async with Client(mcp) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_without_parentheses(self):
        """Test that @tool decorator works without parentheses."""
        mcp = FastMCP()

        # Test the @tool syntax without parentheses
        @mcp.tool
        def add(x: int, y: int) -> int:
            return x + y

        # Verify the tool was registered correctly
        tools = await mcp.get_tools()
        assert "add" in tools

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.tool(name="custom-add")
        def add(x: int, y: int) -> int:
            return x + y

        async with Client(mcp) as client:
            result = await client.call_tool("custom-add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.tool(description="Add two numbers")
        def add(x: int, y: int) -> int:
            return x + y

        tools = await mcp._list_tools_mcp()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.description == "Add two numbers"

    async def test_tool_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, x: int):
                self.x = x

            def add(self, y: int) -> int:
                return self.x + y

        obj = MyClass(10)
        mcp.add_tool(Tool.from_function(obj.add))
        async with Client(mcp) as client:
            result = await client.call_tool("add", {"y": 2})
            assert result.data == 12

    async def test_tool_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            x: int = 10

            @classmethod
            def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        async with Client(mcp) as client:
            result = await client.call_tool("add", {"y": 2})
            assert result.data == 12

    async def test_tool_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add(x: int, y: int) -> int:
                return x + y

        async with Client(mcp) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.tool
        async def add(x: int, y: int) -> int:
            return x + y

        async with Client(mcp) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

            class MyClass:
                @mcp.tool
                @classmethod
                def add(cls, y: int) -> None:
                    pass

    async def test_tool_decorator_classmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            x = 10

            @classmethod
            async def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        async with Client(mcp) as client:
            result = await client.call_tool("add", {"y": 2})
            assert result.data == 12

    async def test_tool_decorator_staticmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            async def add(x: int, y: int) -> int:
                return x + y

        mcp.add_tool(Tool.from_function(MyClass.add))
        async with Client(mcp) as client:
            result = await client.call_tool("add", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_staticmethod_order(self):
        """Test that the recommended decorator order works for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.tool
            @staticmethod
            def add_v1(x: int, y: int) -> int:
                return x + y

        # Test that the recommended order works
        async with Client(mcp) as client:
            result = await client.call_tool("add_v1", {"x": 1, "y": 2})
            assert result.data == 3

    async def test_tool_decorator_with_tags(self):
        """Test that the tool decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.tool(tags={"example", "test-tag"})
        def sample_tool(x: int) -> int:
            return x * 2

        # Verify the tags were set correctly (local inventory)
        tools_dict = await mcp._tool_manager.get_tools()
        assert len(tools_dict) == 1
        only_tool = next(iter(tools_dict.values()))
        assert only_tool.tags == {"example", "test-tag"}

    async def test_add_tool_with_custom_name(self):
        """Test adding a tool with a custom name using server.add_tool()."""
        mcp = FastMCP()

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        mcp.add_tool(Tool.from_function(multiply, name="custom_multiply"))

        # Check that the tool is registered with the custom name
        tools = await mcp.get_tools()
        assert "custom_multiply" in tools

        # Call the tool by its custom name
        async with Client(mcp) as client:
            result = await client.call_tool("custom_multiply", {"a": 5, "b": 3})
            assert result.data == 15

        # Original name should not be registered
        assert "multiply" not in tools

    async def test_tool_with_annotated_arguments(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: Annotated[int, Field(description="x is an int")],
            y: Annotated[str, Field(description="y is not an int")],
        ) -> None:
            pass

        tool = (await mcp.get_tools())["add"]
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_with_field_defaults(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool
        def add(
            x: int = Field(description="x is an int"),
            y: str = Field(description="y is not an int"),
        ) -> None:
            pass

        tool = (await mcp.get_tools())["add"]
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"

    async def test_tool_direct_function_call(self):
        """Test that tools can be registered via direct function call."""
        mcp = FastMCP()

        def standalone_function(x: int, y: int) -> int:
            """A standalone function to be registered."""
            return x + y

        # Register it directly using the new syntax
        result_fn = mcp.tool(standalone_function, name="direct_call_tool")

        # The function should be returned unchanged
        assert isinstance(result_fn, FunctionTool)

        # Verify the tool was registered correctly
        tools = await mcp.get_tools()
        assert tools["direct_call_tool"] is result_fn

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.call_tool("direct_call_tool", {"x": 5, "y": 3})
            assert result.data == 8

    async def test_tool_decorator_with_string_name(self):
        """Test that @tool("custom_name") syntax works correctly."""
        mcp = FastMCP()

        @mcp.tool("string_named_tool")
        def my_function(x: int) -> str:
            """A function with a string name."""
            return f"Result: {x}"

        # Verify the tool was registered with the custom name
        tools = await mcp.get_tools()
        assert "string_named_tool" in tools
        assert "my_function" not in tools  # Original name should not be registered

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.call_tool("string_named_tool", {"x": 42})
            assert result.data == "Result: 42"

    async def test_tool_decorator_conflicting_names_error(self):
        """Test that providing both positional and keyword name raises an error."""
        mcp = FastMCP()

        with pytest.raises(
            TypeError,
            match="Cannot specify both a name as first argument and as keyword argument",
        ):

            @mcp.tool("positional_name", name="keyword_name")
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_output_schema(self):
        mcp = FastMCP()

        with pytest.raises(
            ValueError, match="Output schemas must represent object types"
        ):

            @mcp.tool(output_schema={"type": "integer"})
            def my_function(x: int) -> str:
                return f"Result: {x}"

    async def test_tool_decorator_with_meta(self):
        """Test that meta parameter is passed through the tool decorator."""
        mcp = FastMCP()

        meta_data = {"version": "1.0", "author": "test"}

        @mcp.tool(meta=meta_data)
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        tools_dict = await mcp.get_tools()
        tool = tools_dict["multiply"]

        assert tool.meta == meta_data


class TestResourceDecorator:
    async def test_no_resources_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource("resource://data")

    async def test_resource_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def get_data() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Hello, world!"  # type: ignore[attr-defined]

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
            assert result[0].text == "Hello, world!"  # type: ignore[attr-defined]

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

        mcp.add_resource(
            Resource.from_function(
                obj.get_data, uri="resource://data", name="instance-resource"
            )
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "My prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_resource(
            Resource.from_function(
                MyClass.get_data, uri="resource://data", name="class-resource"
            )
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Class prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

            class MyClass:
                @mcp.resource("resource://data")
                @classmethod
                def get_data(cls) -> None:
                    pass

    async def test_resource_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://data")
            @staticmethod
            def get_data() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Static Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        async def get_data() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Async Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_staticmethod_order(self):
        """Test that both decorator orders work for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://data")  # type: ignore[misc]  # Type checker warns but runtime works
            @staticmethod
            def get_data() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Static Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_with_meta(self):
        """Test that meta parameter is passed through the resource decorator."""
        mcp = FastMCP()

        meta_data = {"version": "1.0", "author": "test"}

        @mcp.resource("resource://data", meta=meta_data)
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resource = resources_dict["resource://data"]

        assert resource.meta == meta_data


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
            assert result[0].text == "Data for test"  # type: ignore[attr-defined]

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
        assert result[0].text == "Data for test"  # type: ignore[attr-defined]

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
        template = ResourceTemplate.from_function(
            obj.get_data,
            uri_template="resource://{name}/data",
            name="instance-template",
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert result[0].text == "My prefix: Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls, name: str) -> str:
                return f"{cls.prefix} Data for {name}"

        template = ResourceTemplate.from_function(
            MyClass.get_data,
            uri_template="resource://{name}/data",
            name="class-template",
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert result[0].text == "Class prefix: Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://{name}/data")
            @staticmethod
            def get_data(name: str) -> str:
                return f"Static Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert result[0].text == "Static Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        async def get_data(name: str) -> str:
            return f"Async Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert result[0].text == "Async Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_with_tags(self):
        """Test that the template decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_template_decorator_wildcard_param(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param*}"]
        assert template.uri_template == "resource://{param*}"
        assert template.name == "template_resource"

    async def test_template_decorator_with_meta(self):
        """Test that meta parameter is passed through the template decorator."""
        mcp = FastMCP()

        meta_data = {"version": "2.0", "template": "test"}

        @mcp.resource("resource://{param}/data", meta=meta_data)
        def get_template_data(param: str) -> str:
            return f"Data for {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}/data"]

        assert template.meta == meta_data


class TestPromptDecorator:
    async def test_prompt_decorator(self):
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
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_without_parentheses(self):
        mcp = FastMCP()

        # This should now work correctly (not raise an error)
        @mcp.prompt  # No parentheses - this is now supported
        def fn() -> str:
            return "Hello, world!"

        # Verify the prompt was registered correctly
        prompts = await mcp.get_prompts()
        assert "fn" in prompts

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.get_prompt("fn")
            assert len(result.messages) == 1
            assert result.messages[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

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
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

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
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_with_parameters(self):
        mcp = FastMCP()

        @mcp.prompt
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
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Hello, World!"  # type: ignore[attr-defined]

            result = await client.get_prompt(
                "test_prompt", {"name": "World", "greeting": "Hi"}
            )
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Hi, World!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def test_prompt(self) -> str:
                return f"{self.prefix} Hello, world!"

        obj = MyClass("My prefix:")
        mcp.add_prompt(Prompt.from_function(obj.test_prompt, name="test_prompt"))

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "My prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def test_prompt(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_prompt(Prompt.from_function(MyClass.test_prompt, name="test_prompt"))

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Class prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

            class MyClass:
                @mcp.prompt
                @classmethod
                def test_prompt(cls) -> None:
                    pass

    async def test_prompt_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.prompt
            @staticmethod
            def test_prompt() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Static Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.prompt
        async def test_prompt() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Async Hello, world!"  # type: ignore[attr-defined]

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

    async def test_prompt_decorator_with_string_name(self):
        """Test that @prompt(\"custom_name\") syntax works correctly."""
        mcp = FastMCP()

        @mcp.prompt("string_named_prompt")
        def my_function() -> str:
            """A function with a string name."""
            return "Hello from string named prompt!"

        # Verify the prompt was registered with the custom name
        prompts = await mcp.get_prompts()
        assert "string_named_prompt" in prompts
        assert "my_function" not in prompts  # Original name should not be registered

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.get_prompt("string_named_prompt")
            assert len(result.messages) == 1
            assert result.messages[0].content.text == "Hello from string named prompt!"  # type: ignore[attr-defined]

    async def test_prompt_direct_function_call(self):
        """Test that prompts can be registered via direct function call."""
        mcp = FastMCP()

        def standalone_function() -> str:
            """A standalone function to be registered."""
            return "Hello from direct call!"

        # Register it directly using the new syntax
        result_fn = mcp.prompt(standalone_function, name="direct_call_prompt")

        # The function should be returned unchanged
        assert isinstance(result_fn, FunctionPrompt)

        # Verify the prompt was registered correctly
        prompts = await mcp.get_prompts()
        assert prompts["direct_call_prompt"] is result_fn

        # Verify it can be called
        async with Client(mcp) as client:
            result = await client.get_prompt("direct_call_prompt")
            assert len(result.messages) == 1
            assert result.messages[0].content.text == "Hello from direct call!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_conflicting_names_error(self):
        """Test that providing both positional and keyword names raises an error."""
        mcp = FastMCP()

        with pytest.raises(
            TypeError,
            match="Cannot specify both a name as first argument and as keyword argument",
        ):

            @mcp.prompt("positional_name", name="keyword_name")
            def my_function() -> str:
                return "Hello, world!"

    async def test_prompt_decorator_staticmethod_order(self):
        """Test that both decorator orders work for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.prompt  # type: ignore[misc]  # Type checker warns but runtime works
            @staticmethod
            def test_prompt() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Static Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_with_meta(self):
        """Test that meta parameter is passed through the prompt decorator."""
        mcp = FastMCP()

        meta_data = {"version": "3.0", "type": "prompt"}

        @mcp.prompt(meta=meta_data)
        def test_prompt(message: str) -> str:
            return f"Response: {message}"

        prompts_dict = await mcp.get_prompts()
        prompt = prompts_dict["test_prompt"]

        assert prompt.meta == meta_data


class TestResourcePrefixHelpers:
    @pytest.mark.parametrize(
        "uri,prefix,expected",
        [
            # Normal paths
            (
                "resource://path/to/resource",
                "prefix",
                "resource://prefix/path/to/resource",
            ),
            # Absolute paths (with triple slash)
            ("resource:///absolute/path", "prefix", "resource://prefix//absolute/path"),
            # Empty prefix should return the original URI
            ("resource://path/to/resource", "", "resource://path/to/resource"),
            # Different protocols
            ("file://path/to/file", "prefix", "file://prefix/path/to/file"),
            ("http://example.com/path", "prefix", "http://prefix/example.com/path"),
            # Prefixes with special characters
            (
                "resource://path/to/resource",
                "pre.fix",
                "resource://pre.fix/path/to/resource",
            ),
            (
                "resource://path/to/resource",
                "pre/fix",
                "resource://pre/fix/path/to/resource",
            ),
            # Empty paths
            ("resource://", "prefix", "resource://prefix/"),
        ],
    )
    def test_add_resource_prefix(self, uri, prefix, expected):
        """Test that add_resource_prefix correctly adds prefixes to URIs."""
        result = add_resource_prefix(uri, prefix)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_uri",
        [
            "not-a-uri",
            "resource:no-slashes",
            "missing-protocol",
            "http:/missing-slash",
        ],
    )
    def test_add_resource_prefix_invalid_uri(self, invalid_uri):
        """Test that add_resource_prefix raises ValueError for invalid URIs."""
        with pytest.raises(ValueError, match="Invalid URI format"):
            add_resource_prefix(invalid_uri, "prefix")

    @pytest.mark.parametrize(
        "uri,prefix,expected",
        [
            # Normal paths
            (
                "resource://prefix/path/to/resource",
                "prefix",
                "resource://path/to/resource",
            ),
            # Absolute paths (with triple slash)
            ("resource://prefix//absolute/path", "prefix", "resource:///absolute/path"),
            # URI without the expected prefix should return the original URI
            (
                "resource://other/path/to/resource",
                "prefix",
                "resource://other/path/to/resource",
            ),
            # Empty prefix should return the original URI
            ("resource://path/to/resource", "", "resource://path/to/resource"),
            # Different protocols
            ("file://prefix/path/to/file", "prefix", "file://path/to/file"),
            # Prefixes with special characters (that need escaping in regex)
            (
                "resource://pre.fix/path/to/resource",
                "pre.fix",
                "resource://path/to/resource",
            ),
            (
                "resource://pre/fix/path/to/resource",
                "pre/fix",
                "resource://path/to/resource",
            ),
            # Empty paths
            ("resource://prefix/", "prefix", "resource://"),
        ],
    )
    def test_remove_resource_prefix(self, uri, prefix, expected):
        """Test that remove_resource_prefix correctly removes prefixes from URIs."""
        result = remove_resource_prefix(uri, prefix)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_uri",
        [
            "not-a-uri",
            "resource:no-slashes",
            "missing-protocol",
            "http:/missing-slash",
        ],
    )
    def test_remove_resource_prefix_invalid_uri(self, invalid_uri):
        """Test that remove_resource_prefix raises ValueError for invalid URIs."""
        with pytest.raises(ValueError, match="Invalid URI format"):
            remove_resource_prefix(invalid_uri, "prefix")

    @pytest.mark.parametrize(
        "uri,prefix,expected",
        [
            # URI with prefix
            ("resource://prefix/path/to/resource", "prefix", True),
            # URI with another prefix
            ("resource://other/path/to/resource", "prefix", False),
            # URI with prefix as a substring but not at path start
            ("resource://path/prefix/resource", "prefix", False),
            # Empty prefix
            ("resource://path/to/resource", "", False),
            # Different protocols
            ("file://prefix/path/to/file", "prefix", True),
            # Prefix with special characters
            ("resource://pre.fix/path/to/resource", "pre.fix", True),
            # Empty paths
            ("resource://prefix/", "prefix", True),
        ],
    )
    def test_has_resource_prefix(self, uri, prefix, expected):
        """Test that has_resource_prefix correctly identifies prefixes in URIs."""
        result = has_resource_prefix(uri, prefix)
        assert result == expected

    @pytest.mark.parametrize(
        "invalid_uri",
        [
            "not-a-uri",
            "resource:no-slashes",
            "missing-protocol",
            "http:/missing-slash",
        ],
    )
    def test_has_resource_prefix_invalid_uri(self, invalid_uri):
        """Test that has_resource_prefix raises ValueError for invalid URIs."""
        with pytest.raises(ValueError, match="Invalid URI format"):
            has_resource_prefix(invalid_uri, "prefix")


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
            assert result[0].text == "Resource content"  # type: ignore[attr-defined]

            # Absolute path resource
            result = await client.read_resource("resource://prefix//absolute/path")
            assert result[0].text == "Absolute resource content"  # type: ignore[attr-defined]

            # Template resource
            result = await client.read_resource(
                "resource://prefix/param-value/template"
            )
            assert result[0].text == "Template resource with param-value"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "uri,prefix,expected_match,expected_strip",
        [
            # Regular resource
            (
                "resource://prefix/path/to/resource",
                "prefix",
                True,
                "resource://path/to/resource",
            ),
            # Absolute path
            (
                "resource://prefix//absolute/path",
                "prefix",
                True,
                "resource:///absolute/path",
            ),
            # Non-matching prefix
            (
                "resource://other/path/to/resource",
                "prefix",
                False,
                "resource://other/path/to/resource",
            ),
            # Different protocol
            ("http://prefix/example.com", "prefix", True, "http://example.com"),
        ],
    )
    async def test_mounted_server_matching_and_stripping(
        self, uri, prefix, expected_match, expected_strip
    ):
        """Test that resource prefix utility functions correctly match and strip resource prefixes."""
        from fastmcp.server.server import has_resource_prefix, remove_resource_prefix

        # Test matching
        assert has_resource_prefix(uri, prefix) == expected_match

        # Test stripping
        assert remove_resource_prefix(uri, prefix) == expected_strip

    async def test_import_server_with_new_prefix_format(self):
        """Test that import_server correctly uses the new prefix format."""
        # Create a server with resources
        source_server = FastMCP(name="SourceServer")

        @source_server.resource("resource://test-resource")
        def get_resource():
            return "Resource content"

        @source_server.resource("resource:///absolute/path")
        def get_absolute_resource():
            return "Absolute resource content"

        @source_server.resource("resource://{param}/template")
        def get_template_resource(param: str):
            return f"Template resource with {param}"

        # Create target server and import the source server
        target_server = FastMCP(name="TargetServer")
        await target_server.import_server(source_server, "imported")

        # Check that the resources were imported with the correct prefixes
        resources = await target_server.get_resources()
        templates = await target_server.get_resource_templates()

        assert "resource://imported/test-resource" in resources
        assert "resource://imported//absolute/path" in resources
        assert "resource://imported/{param}/template" in templates

        # Verify we can access the resources
        async with Client(target_server) as client:
            result = await client.read_resource("resource://imported/test-resource")
            assert result[0].text == "Resource content"  # type: ignore[attr-defined]

            result = await client.read_resource("resource://imported//absolute/path")
            assert result[0].text == "Absolute resource content"  # type: ignore[attr-defined]

            result = await client.read_resource(
                "resource://imported/param-value/template"
            )
            assert result[0].text == "Template resource with param-value"  # type: ignore[attr-defined]


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
            assert result.content[0].text == "Hello, World!"  # type: ignore[attr-defined]
