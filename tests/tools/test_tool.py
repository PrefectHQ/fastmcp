import pytest
from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import Tool, _convert_to_content
from fastmcp.utilities.tests import temporary_settings
from fastmcp.utilities.types import Audio, File, Image


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

    async def test_tool_with_audio_return(self):
        def audio_tool(data: bytes) -> Audio:
            return Audio(data=data)

        tool = Tool.from_function(audio_tool)

        result = await tool.run({"data": "test.wav"})
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert isinstance(result[0], AudioContent)

    async def test_tool_with_file_return(self):
        def file_tool(data: bytes) -> File:
            return File(data=data, format="octet-stream")

        tool = Tool.from_function(file_tool)

        result = await tool.run({"data": "test.bin"})
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert len(result) == 1
        assert isinstance(result[0], EmbeddedResource)
        assert result[0].type == "resource"
        assert hasattr(result[0], "resource")
        resource = result[0].resource
        assert resource.mimeType == "application/octet-stream"

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

            @classmethod
            def call(cls, x: int, y: int) -> int:
                """Add two numbers."""
                return x + y

        tool = Tool.from_function(MyClass.call)
        assert tool.name == "call"
        assert tool.description == "Add two numbers."
        assert "x" in tool.parameters["properties"]
        assert "y" in tool.parameters["properties"]

    async def test_tool_serializer(self):
        """Test that a tool's serializer is used to serialize the result."""

        def custom_serializer(data) -> str:
            return f"Custom serializer: {data}"

        def process_list(items: list[int]) -> int:
            return sum(items)

        tool = Tool.from_function(process_list, serializer=custom_serializer)

        result = await tool.run(arguments={"items": [1, 2, 3, 4, 5]})
        assert isinstance(result[0], TextContent)
        assert result[0].text == "Custom serializer: 15"


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

        @mcp.tool
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

        @mcp.tool
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

        @mcp.tool
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

        @mcp.tool
        def process_set(items: set[int]) -> int:
            assert isinstance(items, set)
            return sum(items)

        async with Client(mcp) as client:
            result = await client.call_tool("process_set", {"items": "[1, 2, 3, 4, 5]"})
            assert result[0].text == "15"  # type: ignore[attr-dict]

    async def test_tool_tuple_coercion(self):
        """Test JSON string to tuple type coercion."""
        mcp = FastMCP()

        @mcp.tool
        def process_tuple(items: tuple[int, str]) -> int:
            assert isinstance(items, tuple)
            return items[0] + len(items[1])

        async with Client(mcp) as client:
            result = await client.call_tool("process_tuple", {"items": '["1", "two"]'})
            assert isinstance(result[0], TextContent)
            assert result[0].text == "4"  # type: ignore[attr-dict]


class TestConvertResultToContent:
    """Tests for the _convert_to_content helper function."""

    def test_none_result(self):
        """Test that None results in an empty list."""
        result = _convert_to_content(None)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_text_content_result(self):
        """Test that TextContent is returned as a list containing itself."""
        content = TextContent(type="text", text="hello")
        result = _convert_to_content(content)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is content

    def test_image_content_result(self):
        """Test that ImageContent is returned as a list containing itself."""
        content = ImageContent(type="image", data="fakeimagedata", mimeType="image/png")
        result = _convert_to_content(content)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is content

    def test_embedded_resource_result(self):
        """Test that EmbeddedResource is returned as a list containing itself."""
        content = EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=AnyUrl("resource://test"),
                mimeType="text/plain",
                text="resource content",
            ),
        )
        result = _convert_to_content(content)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is content

    def test_image_object_result(self):
        """Test that an Image object is converted to ImageContent."""
        image_obj = Image(data=b"fakeimagedata")

        result = _convert_to_content(image_obj)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ImageContent)
        assert result[0].data == "ZmFrZWltYWdlZGF0YQ=="

    def test_audio_object_result(self):
        """Test that an Audio object is converted to AudioContent."""
        audio_obj = Audio(data=b"fakeaudiodata")

        result = _convert_to_content(audio_obj)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], AudioContent)
        assert result[0].data == "ZmFrZWF1ZGlvZGF0YQ=="

    def test_file_object_result(self):
        """Test that a File object is converted to EmbeddedResource with BlobResourceContents."""
        file_obj = File(data=b"filedata", format="octet-stream")

        result = _convert_to_content(file_obj)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], EmbeddedResource)
        assert result[0].type == "resource"
        assert hasattr(result[0], "resource")
        resource = result[0].resource
        assert resource.mimeType == "application/octet-stream"
        # Check for blob attribute and its value
        assert hasattr(resource, "blob")
        assert getattr(resource, "blob") == "ZmlsZWRhdGE="  # base64 encoded "filedata"
        # Convert URI to string for startswith check
        assert str(resource.uri).startswith("file:///resource.octet-stream")

    def test_file_object_text_result(self):
        """Test that a File object with text data is converted to EmbeddedResource with TextResourceContents."""
        file_obj = File(data=b"sometext", format="plain")
        result = _convert_to_content(file_obj)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], EmbeddedResource)
        assert result[0].type == "resource"
        resource = result[0].resource
        assert isinstance(resource, TextResourceContents)
        assert resource.mimeType == "text/plain"
        assert resource.text == "sometext"

    def test_basic_type_result(self):
        """Test that a basic type is converted to TextContent."""
        result = _convert_to_content(123)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "123"

        result = _convert_to_content("hello")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "hello"

        result = _convert_to_content({"a": 1, "b": 2})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '{\n  "a": 1,\n  "b": 2\n}'

    def test_list_of_basic_types(self):
        """Test that a list of basic types is converted to a single TextContent."""
        result = _convert_to_content([1, "two", {"c": 3}])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '[\n  1,\n  "two",\n  {\n    "c": 3\n  }\n]'

    def test_list_of_mcp_types(self):
        """Test that a list of MCP types is returned as a list of those types."""
        content1 = TextContent(type="text", text="hello")
        content2 = ImageContent(
            type="image", data="fakeimagedata2", mimeType="image/png"
        )
        result = _convert_to_content([content1, content2])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] is content1
        assert result[1] is content2

    def test_list_of_mixed_types(self):
        """Test that a list of mixed types is converted correctly."""
        content1 = TextContent(type="text", text="hello")
        image_obj = Image(data=b"fakeimagedata")
        basic_data = {"a": 1}
        result = _convert_to_content([content1, image_obj, basic_data])

        assert isinstance(result, list)
        assert len(result) == 3

        text_content_count = sum(isinstance(item, TextContent) for item in result)
        image_content_count = sum(isinstance(item, ImageContent) for item in result)

        assert text_content_count == 2
        assert image_content_count == 1

        text_item = next(item for item in result if isinstance(item, TextContent))
        assert text_item.text == '{\n  "a": 1\n}'

        image_item = next(item for item in result if isinstance(item, ImageContent))
        assert image_item.data == "ZmFrZWltYWdlZGF0YQ=="

    def test_list_of_mixed_types_list(self):
        """Test that a list of mixed types, including a list as one of the elements, is converted correctly."""
        content1 = TextContent(type="text", text="hello")
        image_obj = Image(data=b"fakeimagedata")
        basic_data = [{"a": 1}, {"b": 2}]
        result = _convert_to_content([content1, image_obj, basic_data])

        assert isinstance(result, list)
        assert len(result) == 3

        text_content_count = sum(isinstance(item, TextContent) for item in result)
        image_content_count = sum(isinstance(item, ImageContent) for item in result)

        assert text_content_count == 2
        assert image_content_count == 1

        text_item = next(item for item in result if isinstance(item, TextContent))
        assert text_item.text == '[\n  {\n    "a": 1\n  },\n  {\n    "b": 2\n  }\n]'

        image_item = next(item for item in result if isinstance(item, ImageContent))
        assert image_item.data == "ZmFrZWltYWdlZGF0YQ=="

    def test_list_of_mixed_types_with_audio(self):
        """Test that a list of mixed types including Audio is converted correctly."""
        content1 = TextContent(type="text", text="hello")
        audio_obj = Audio(data=b"fakeaudiodata")
        basic_data = {"a": 1}
        result = _convert_to_content([content1, audio_obj, basic_data])

        assert isinstance(result, list)
        assert len(result) == 3

        text_content_count = sum(isinstance(item, TextContent) for item in result)
        audio_content_count = sum(isinstance(item, AudioContent) for item in result)

        assert text_content_count == 2
        assert audio_content_count == 1

        text_item = next(item for item in result if isinstance(item, TextContent))
        assert text_item.text == '{\n  "a": 1\n}'

        audio_item = next(item for item in result if isinstance(item, AudioContent))
        assert audio_item.data == "ZmFrZWF1ZGlvZGF0YQ=="

    def test_list_of_mixed_types_with_file(self):
        """Test that a list of mixed types including File is converted correctly."""
        content1 = TextContent(type="text", text="hello")
        file_obj = File(data=b"filedata", format="octet-stream")
        basic_data = {"a": 1}
        result = _convert_to_content([content1, file_obj, basic_data])

        assert isinstance(result, list)
        assert len(result) == 3

        text_content_count = sum(isinstance(item, TextContent) for item in result)
        embedded_content_count = sum(
            isinstance(item, EmbeddedResource) and item.type == "resource"
            for item in result
        )

        assert text_content_count == 2
        assert embedded_content_count == 1

        text_item = next(item for item in result if isinstance(item, TextContent))
        assert text_item.text == '{\n  "a": 1\n}'

        embedded_item = next(
            item
            for item in result
            if isinstance(item, EmbeddedResource) and item.type == "resource"
        )
        resource = embedded_item.resource
        assert resource.mimeType == "application/octet-stream"
        # Check for blob attribute and its value
        assert hasattr(resource, "blob")
        assert getattr(resource, "blob") == "ZmlsZWRhdGE="

    def test_empty_list(self):
        """Test that an empty list results in an empty list."""
        result = _convert_to_content([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_empty_dict(self):
        """Test that an empty dictionary is converted to TextContent."""
        result = _convert_to_content({})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "{}"

    def test_with_custom_serializer(self):
        """Test that a custom serializer is used for non-MCP types."""

        def custom_serializer(data):
            return f"Serialized: {data}"

        result = _convert_to_content({"a": 1}, serializer=custom_serializer)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "Serialized: {'a': 1}"

    def test_custom_serializer_error_fallback(self, caplog):
        """Test that if a custom serializer fails, it falls back to the default."""
        import logging

        def custom_serializer_that_fails(data):
            raise ValueError("Serialization failed")

        with caplog.at_level(logging.WARNING):
            result = _convert_to_content(
                {"a": 1}, serializer=custom_serializer_that_fails
            )

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        # Should fall back to default serializer (pydantic_core.to_json)
        assert result[0].text == '{\n  "a": 1\n}'
        assert "Error serializing tool result" in caplog.text

    def test_process_as_single_item_flag(self):
        """Test that _process_as_single_item forces list to be treated as one item."""

        result = _convert_to_content([1, "two", {"c": 3}], _process_as_single_item=True)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '[\n  1,\n  "two",\n  {\n    "c": 3\n  }\n]'

        content1 = TextContent(type="text", text="hello")
        result = _convert_to_content([1, content1], _process_as_single_item=True)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)

        assert (
            result[0].text
            == '[\n  1,\n  {\n    "type": "text",\n    "text": "hello",\n    "annotations": null\n  }\n]'
        )
