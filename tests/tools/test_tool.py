import pytest
from mcp.types import ImageContent
from pydantic import BaseModel

from fastmcp import FastMCP, Image
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import Tool
from fastmcp.utilities.tests import temporary_settings


class TestToolFromFunction:
    def test_basic_function(self):
        """Test registering and running a basic function."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        tool = Tool.from_function(add)

        assert tool.name == "add"
        assert tool.description == "Add two numbers."
        assert len(tool.parameters["properties"]) == 2
        assert tool.parameters["properties"]["a"]["type"] == "integer"
        assert tool.parameters["properties"]["b"]["type"] == "integer"

    async def test_async_function(self):
        """Test registering and running an async function."""

        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f"Data from {url}"

        tool = Tool.from_function(fetch_data)

        assert tool.name == "fetch_data"
        assert tool.description == "Fetch data from URL."
        assert tool.parameters["properties"]["url"]["type"] == "string"

    def test_callable_object(self):
        class Adder:
            """Adds two numbers."""

            def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())
        assert tool.name == "Adder"
        assert tool.description == "Adds two numbers."
        assert len(tool.parameters["properties"]) == 2
        assert tool.parameters["properties"]["x"]["type"] == "integer"
        assert tool.parameters["properties"]["y"]["type"] == "integer"

    def test_async_callable_object(self):
        class Adder:
            """Adds two numbers."""

            async def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())
        assert tool.name == "Adder"
        assert tool.description == "Adds two numbers."
        assert len(tool.parameters["properties"]) == 2
        assert tool.parameters["properties"]["x"]["type"] == "integer"
        assert tool.parameters["properties"]["y"]["type"] == "integer"

    def test_pydantic_model_function(self):
        """Test registering a function that takes a Pydantic model."""

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput, flag: bool) -> dict:
            """Create a new user."""
            return {"id": 1, **user.model_dump()}

        tool = Tool.from_function(create_user)

        assert tool.name == "create_user"
        assert tool.description == "Create a new user."
        assert "name" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "age" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "flag" in tool.parameters["properties"]

    async def test_tool_with_image_return(self):
        def image_tool(data: bytes) -> Image:
            return Image(data=data)

        tool = Tool.from_function(image_tool)

        result = await tool.run({"data": "test.png"})
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert isinstance(result[0], ImageContent)

    def test_non_callable_fn(self):
        with pytest.raises(TypeError, match="not a callable object"):
            Tool.from_function(1)  # type: ignore

    def test_lambda(self):
        tool = Tool.from_function(lambda x: x, name="my_tool")
        assert tool.name == "my_tool"

    def test_lambda_with_no_name(self):
        with pytest.raises(
            ValueError, match="You must provide a name for lambda functions"
        ):
            Tool.from_function(lambda x: x)

    def test_private_arguments(self):
        def add(_a: int, _b: int) -> int:
            """Add two numbers."""
            return _a + _b

        tool = Tool.from_function(add)
        assert tool.parameters["properties"]["_a"]["type"] == "integer"
        assert tool.parameters["properties"]["_b"]["type"] == "integer"

    def test_tool_with_varargs_not_allowed(self):
        def func(a: int, b: int, *args: int) -> int:
            """Add two numbers."""
            return a + b

        with pytest.raises(
            ValueError, match=r"Functions with \*args are not supported as tools"
        ):
            Tool.from_function(func)

    def test_tool_with_varkwargs_not_allowed(self):
        def func(a: int, b: int, **kwargs: int) -> int:
            """Add two numbers."""
            return a + b

        with pytest.raises(
            ValueError, match=r"Functions with \*\*kwargs are not supported as tools"
        ):
            Tool.from_function(func)

    async def test_instance_method(self):
        class MyClass:
            def add(self, x: int, y: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        tool = Tool.from_function(obj.add)
        assert tool.name == "add"
        assert tool.description == "Add two numbers."
        assert "self" not in tool.parameters["properties"]

    async def test_instance_method_with_varargs_not_allowed(self):
        class MyClass:
            def add(self, x: int, y: int, *args: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        with pytest.raises(
            ValueError, match=r"Functions with \*args are not supported as tools"
        ):
            Tool.from_function(obj.add)

    async def test_instance_method_with_varkwargs_not_allowed(self):
        class MyClass:
            def add(self, x: int, y: int, **kwargs: int) -> int:
                """Add two numbers."""
                return x + y

        obj = MyClass()

        with pytest.raises(
            ValueError, match=r"Functions with \*\*kwargs are not supported as tools"
        ):
            Tool.from_function(obj.add)

    async def test_classmethod(self):
        class MyClass:
            x: int = 10


class TestLegacyToolJsonParsing:
    """Tests for Tool's JSON pre-parsing functionality."""

    @pytest.fixture(autouse=True)
    def enable_legacy_json_parsing(self):
        with temporary_settings(tool_attempt_parse_json_args=True):
            yield

    async def test_json_string_arguments(self):
        """Test that JSON string arguments are parsed and validated correctly"""

        def simple_func(x: int, y: list[str]) -> str:
            return f"{x}-{','.join(y)}"

        # Create a tool to use its JSON pre-parsing logic
        tool = Tool.from_function(simple_func)

        # Prepare arguments where some are JSON strings
        json_args = {
            "x": 1,
            "y": '["a", "b", "c"]',  # JSON string
        }

        # Run the tool which will do JSON parsing
        result = await tool.run(json_args)
        assert result[0].text == "1-a,b,c"  # type: ignore[attr-dict]

    async def test_str_vs_list_str(self):
        """Test handling of string vs list[str] type annotations."""

        def func_with_str_types(str_or_list: str | list[str]) -> str | list[str]:
            return str_or_list

        tool = Tool.from_function(func_with_str_types)

        # Test regular string input (should remain a string)
        result = await tool.run({"str_or_list": "hello"})
        assert result[0].text == "hello"  # type: ignore[attr-dict]

        # Test JSON string input (should be parsed as a string)
        result = await tool.run({"str_or_list": '"hello"'})
        assert result[0].text == "hello"  # type: ignore[attr-dict]

        # Test JSON list input (should be parsed as a list)
        result = await tool.run({"str_or_list": '["hello", "world"]'})

        # The exact formatting might vary, so we just check that it contains the key elements
        text_without_whitespace = result[0].text.replace(" ", "").replace("\n", "")  # type: ignore[attr-dict]
        assert "hello" in text_without_whitespace
        assert "world" in text_without_whitespace
        assert "[" in text_without_whitespace
        assert "]" in text_without_whitespace

    async def test_keep_str_as_str(self):
        """Test that string arguments are kept as strings when they're not valid JSON"""

        def func_with_str_types(string: str) -> str:
            return string

        tool = Tool.from_function(func_with_str_types)

        # Invalid JSON should remain a string
        invalid_json = "{'nice to meet you': 'hello', 'goodbye': 5}"
        result = await tool.run({"string": invalid_json})
        assert result[0].text == invalid_json  # type: ignore[attr-dict]

    async def test_keep_str_union_as_str(self):
        """Test that string arguments are kept as strings when parsing would create an invalid value"""

        def func_with_str_types(
            string: str | dict[int, str] | None,
        ) -> str | dict[int, str] | None:
            return string

        tool = Tool.from_function(func_with_str_types)

        # Invalid JSON for the union type should remain a string
        invalid_json = "{'nice to meet you': 'hello', 'goodbye': 5}"
        result = await tool.run({"string": invalid_json})
        assert result[0].text == invalid_json  # type: ignore[attr-dict]

    async def test_complex_type_validation(self):
        """Test that parsed JSON is validated against complex types"""

        class SomeModel(BaseModel):
            x: int
            y: dict[int, str]

        def func_with_complex_type(data: SomeModel) -> SomeModel:
            return data

        tool = Tool.from_function(func_with_complex_type)

        # Valid JSON for the model
        valid_json = '{"x": 1, "y": {"1": "hello"}}'
        result = await tool.run({"data": valid_json})
        assert '"x": 1' in result[0].text  # type: ignore[attr-dict]
        assert '"y": {' in result[0].text  # type: ignore[attr-dict]
        assert '"1": "hello"' in result[0].text  # type: ignore[attr-dict]

        # Invalid JSON for the model (y has string keys, not int keys)
        # Should throw a validation error
        invalid_json = '{"x": 1, "y": {"invalid": "hello"}}'
        with pytest.raises(Exception):
            await tool.run({"data": invalid_json})

    async def test_tool_list_coercion(self):
        """Test JSON string to collection type coercion."""
        mcp = FastMCP()

        @mcp.tool()
        def process_list(items: list[int]) -> int:
            return sum(items)

        async with Client(mcp) as client:
            # JSON array string should be coerced to list
            result = await client.call_tool(
                "process_list", {"items": "[1, 2, 3, 4, 5]"}
            )
            assert result[0].text == "15"  # type: ignore[attr-dict]

    async def test_tool_list_coercion_error(self):
        """Test that a list coercion error is raised if the input is not a valid list."""
        mcp = FastMCP()

        @mcp.tool()
        def process_list(items: list[int]) -> int:
            return sum(items)

        async with Client(mcp) as client:
            with pytest.raises(
                ToolError,
                match="Error calling tool 'process_list'",
            ):
                await client.call_tool("process_list", {"items": "['a', 'b', 3]"})

    async def test_tool_dict_coercion(self):
        """Test JSON string to dict type coercion."""
        mcp = FastMCP()

        @mcp.tool()
        def process_dict(data: dict[str, int]) -> int:
            return sum(data.values())

        async with Client(mcp) as client:
            # JSON object string should be coerced to dict
            result = await client.call_tool(
                "process_dict", {"data": '{"a": 1, "b": "2", "c": 3}'}
            )
            assert result[0].text == "6"  # type: ignore[attr-dict]

    async def test_tool_set_coercion(self):
        """Test JSON string to set type coercion."""
        mcp = FastMCP()

        @mcp.tool()
        def process_set(items: set[int]) -> int:
            assert isinstance(items, set)
            return sum(items)

        async with Client(mcp) as client:
            result = await client.call_tool("process_set", {"items": "[1, 2, 3, 4, 5]"})
            assert result[0].text == "15"  # type: ignore[attr-dict]

    async def test_tool_tuple_coercion(self):
        """Test JSON string to tuple type coercion."""
        mcp = FastMCP()

        @mcp.tool()
        def process_tuple(items: tuple[int, str]) -> int:
            assert isinstance(items, tuple)
            return items[0] + len(items[1])

        async with Client(mcp) as client:
            result = await client.call_tool("process_tuple", {"items": '["1", "two"]'})
            assert result[0].text == "4"  # type: ignore[attr-dict]
