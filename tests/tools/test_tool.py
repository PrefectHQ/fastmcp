import json
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import pytest
from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter

from fastmcp.tools.tool import Tool, _convert_to_content
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
        # With primitive wrapping, int return type becomes object with value property
        expected_schema = {
            "type": "object",
            "properties": {"value": {"title": "Value", "type": "integer"}},
            "required": ["value"],
            "title": "Result",
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

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
        assert isinstance(result.content[0], ImageContent)

    async def test_tool_with_audio_return(self):
        def audio_tool(data: bytes) -> Audio:
            return Audio(data=data)

        tool = Tool.from_function(audio_tool)

        result = await tool.run({"data": "test.wav"})
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert isinstance(result.content[0], AudioContent)

    async def test_tool_with_file_return(self):
        def file_tool(data: bytes) -> File:
            return File(data=data, format="octet-stream")

        tool = Tool.from_function(file_tool)

        result = await tool.run({"data": "test.bin"})
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert len(result.content) == 1
        assert isinstance(result.content[0], EmbeddedResource)
        assert result.content[0].type == "resource"
        assert hasattr(result.content[0], "resource")
        resource = result.content[0].resource
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
        # Custom serializer affects unstructured content
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Custom serializer: 15"
        # Structured output should have the raw value
        assert result.structured_content == {"value": 15}


class TestToolFromFunctionOutputSchema:
    async def test_no_return_annotation(self):
        def func():
            pass

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    @pytest.mark.parametrize(
        "annotation",
        [
            None,
            int,
            float,
            bool,
            str,
            int | float,
            list[int],
            list[int | float],
            dict[str, int | None],
            tuple[int, str],
            set[int],
            list[tuple[int, str]],
        ],
    )
    async def test_simple_return_annotation(self, annotation):
        def func() -> annotation:  # type: ignore
            return 1

        tool = Tool.from_function(func)

        base_schema = TypeAdapter(annotation).json_schema()

        # Only pure primitives (just type + optional title) get wrapped
        primitive_types = {"string", "number", "integer", "boolean", "null"}
        schema_type = base_schema.get("type")
        is_pure_primitive = (
            schema_type in primitive_types
            and len(base_schema) <= 2  # Only 'type' and optionally 'title'
            and all(key in {"type", "title"} for key in base_schema.keys())
        )

        if is_pure_primitive:
            # Pure primitives get wrapped
            expected_schema = {
                "type": "object",
                "properties": {"value": base_schema | {"title": "Value"}},
                "required": ["value"],
                "title": "Result",
                "x-fastmcp-wrap-result": True,
            }
            assert tool.output_schema == expected_schema
        else:
            # Complex types (objects, unions, constrained types) remain unwrapped
            assert tool.output_schema == base_schema

    @pytest.mark.parametrize(
        "annotation",
        [
            Any,
            AnyUrl,
            Annotated[int, Field(ge=1)],
            Annotated[int, Field(ge=1)],
        ],
    )
    async def test_complex_return_annotation(self, annotation):
        def func() -> annotation:  # type: ignore
            return 1

        tool = Tool.from_function(func)
        base_schema = TypeAdapter(annotation).json_schema()

        # Complex types with constraints are not wrapped - they remain as-is
        assert tool.output_schema == base_schema

    @pytest.mark.parametrize(
        "annotation, expected",
        [
            (Image, ImageContent),
            (Audio, AudioContent),
            (File, EmbeddedResource),
            (Image | int, ImageContent | int),
            (Image | Audio, ImageContent | AudioContent),
            (list[Image | Audio], list[ImageContent | AudioContent]),
        ],
    )
    async def test_converted_return_annotation(self, annotation, expected):
        def func() -> annotation:  # type: ignore
            return 1

        tool = Tool.from_function(func)
        # Image, Audio, File types don't generate output schemas since they're converted to content directly
        assert tool.output_schema is None

    async def test_dataclass_return_annotation(self):
        @dataclass
        class Person:
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        assert tool.output_schema == TypeAdapter(Person).json_schema()

    async def test_base_model_return_annotation(self):
        class Person(BaseModel):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        assert tool.output_schema == TypeAdapter(Person).json_schema()

    async def test_typeddict_return_annotation(self):
        class Person(TypedDict):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        assert tool.output_schema == TypeAdapter(Person).json_schema()

    async def test_unserializable_return_annotation(self):
        class Unserializable:
            def __init__(self, data: Any):
                self.data = data

        def func() -> Unserializable:
            return Unserializable(data="test")

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    async def test_mixed_unserializable_return_annotation(self):
        class Unserializable:
            def __init__(self, data: Any):
                self.data = data

        def func() -> Unserializable | int:
            return Unserializable(data="test")

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    async def test_provided_output_schema_takes_precedence_over_json_compatible_annotation(
        self,
    ):
        """Test that provided output_schema takes precedence over inferred schema from JSON-compatible annotation."""

        def func() -> dict[str, int]:
            return {"a": 1, "b": 2}

        # Provide a custom output schema that differs from the inferred one
        custom_schema = {"type": "string", "description": "Custom schema"}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_complex_annotation(
        self,
    ):
        """Test that provided output_schema takes precedence over inferred schema from complex annotation."""

        def func() -> list[dict[str, int | float]]:
            return [{"a": 1, "b": 2.5}]

        # Provide a custom output schema that differs from the inferred one
        custom_schema = {"type": "object", "properties": {"custom": {"type": "string"}}}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_unserializable_annotation(
        self,
    ):
        """Test that provided output_schema takes precedence over None schema from unserializable annotation."""

        class Unserializable:
            def __init__(self, data: Any):
                self.data = data

        def func() -> Unserializable:
            return Unserializable(data="test")

        # Provide a custom output schema even though the annotation is unserializable
        custom_schema = {"type": "array", "items": {"type": "string"}}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_no_annotation(self):
        """Test that provided output_schema takes precedence over None schema from no annotation."""

        def func():
            return "hello"

        # Provide a custom output schema even though there's no return annotation
        custom_schema = {"type": "number", "minimum": 0}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_converted_annotation(
        self,
    ):
        """Test that provided output_schema takes precedence over converted schema from Image/Audio/File annotations."""

        def func() -> Image:
            return Image(data=b"test")

        # Provide a custom output schema that differs from the converted ImageContent schema
        custom_schema = {
            "type": "object",
            "properties": {"custom_image": {"type": "string"}},
        }

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_union_annotation(self):
        """Test that provided output_schema takes precedence over inferred schema from union annotation."""

        def func() -> str | int | None:
            return "hello"

        # Provide a custom output schema that differs from the inferred union schema
        custom_schema = {"type": "boolean"}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_pydantic_annotation(
        self,
    ):
        """Test that provided output_schema takes precedence over inferred schema from Pydantic model annotation."""

        class Person(BaseModel):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        # Provide a custom output schema that differs from the inferred Person schema
        custom_schema = {"type": "array", "items": {"type": "number"}}

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema


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
        assert json.loads(result[0].text) == {"a": 1}
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

        assert json.loads(result[0].text) == [
            1,
            {"type": "text", "text": "hello", "annotations": None, "_meta": None},
        ]
