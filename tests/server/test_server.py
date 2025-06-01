from typing import Annotated

import pytest
from mcp import McpError
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.server.server import (
    MountedServer,
    add_resource_prefix,
    has_resource_prefix,
    remove_resource_prefix,
)
from fastmcp.tools.tool import Tool


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
            assert content.text == "¡Hola, 世界! 👋"  # type: ignore[attr-defined]


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
            await mcp._mcp_call_tool("adder", {"a": 1, "b": 2})

    async def test_add_tool_at_init(self):
        def f(x: int) -> int:
            return x + 1

        def g(x: int) -> int:
            """add two to a number"""
            return x + 2

        g_tool = Tool.from_function(g, name="g-tool")

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
            await mcp._mcp_call_tool("add", {"x": 1, "y": 2})

    async def test_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool()
        def add(x: int, y: int) -> int:
            return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert result[0].text == "3"  # type: ignore[attr-defined]

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
        assert result[0].text == "3"  # type: ignore[attr-defined]

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
        assert result[0].text == "12"  # type: ignore[attr-defined]

    async def test_tool_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            x: int = 10

            @classmethod
            def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"y": 2})
        assert result[0].text == "12"  # type: ignore[attr-defined]

    async def test_tool_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.tool()
            def add(x: int, y: int) -> int:
                return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert result[0].text == "3"  # type: ignore[attr-defined]

    async def test_tool_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.tool()
        async def add(x: int, y: int) -> int:
            return x + y

        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert result[0].text == "3"  # type: ignore[attr-defined]

    async def test_tool_decorator_classmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            x = 10

            @classmethod
            async def add(cls, y: int) -> int:
                return cls.x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"y": 2})
        assert result[0].text == "12"  # type: ignore[attr-defined]

    async def test_tool_decorator_staticmethod_async_function(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            async def add(x: int, y: int) -> int:
                return x + y

        mcp.add_tool(MyClass.add)
        result = await mcp._mcp_call_tool("add", {"x": 1, "y": 2})
        assert result[0].text == "3"  # type: ignore[attr-defined]

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
        assert result[0].text == "15"  # type: ignore[attr-defined]

        # Original name should not be registered
        assert "multiply" not in tools

    async def test_tool_with_annotated_arguments(self):
        """Test that tools with annotated arguments work correctly."""
        mcp = FastMCP()

        @mcp.tool()
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

        @mcp.tool()
        def add(
            x: int = Field(description="x is an int"),
            y: str = Field(description="y is not an int"),
        ) -> None:
            pass

        tool = (await mcp.get_tools())["add"]
        assert tool.parameters["properties"]["x"]["description"] == "x is an int"
        assert tool.parameters["properties"]["y"]["description"] == "y is not an int"


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
        mcp.add_resource_fn(
            obj.get_data, uri="resource://data", name="instance-resource"
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

        mcp.add_resource_fn(
            MyClass.get_data, uri="resource://data", name="class-resource"
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "Class prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_resource_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.resource("resource://data")
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
        mcp.add_resource_fn(
            obj.get_data, uri="resource://{name}/data", name="instance-template"
        )

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

        mcp.add_resource_fn(
            MyClass.get_data,
            uri="resource://{name}/data",
            name="class-template",
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert result[0].text == "Class prefix: Data for test"  # type: ignore[attr-defined]

    async def test_template_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.resource("resource://{name}/data")
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
        assert content[0].content.text == "Hello, world!"  # type: ignore[attr-defined]

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
        mcp.add_prompt(obj.test_prompt, name="test_prompt")

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

        mcp.add_prompt(MyClass.test_prompt, name="test_prompt")

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Class prefix: Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @staticmethod
            @mcp.prompt()
            def test_prompt() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.content.text == "Static Hello, world!"  # type: ignore[attr-defined]

    async def test_prompt_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.prompt()
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
        main_server.mount("prefix", server)

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
        """Test that MountedServer correctly matches and strips resource prefixes."""
        # Create a basic server to mount
        server = FastMCP()
        mounted = MountedServer(prefix=prefix, server=server)

        # Test matching
        assert mounted.match_resource(uri) == expected_match

        # Test stripping
        assert mounted.strip_resource_prefix(uri) == expected_strip

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
        await target_server.import_server("imported", source_server)

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
