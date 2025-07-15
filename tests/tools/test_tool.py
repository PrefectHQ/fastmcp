import json
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp.tools.tool import Tool, _convert_to_content
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.tests import caplog_for_fastmcp
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
        # With primitive wrapping, int return type becomes object with result property
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "integer", "title": "Result"}},
            "required": ["result"],
            "title": "_WrappedResult",
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
        assert result.structured_content == {"result": 15}


class TestToolFromFunctionOutputSchema:
    async def test_no_return_annotation(self):
        def func():
            pass

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    @pytest.mark.parametrize(
        "annotation",
        [
            int,
            float,
            bool,
            str,
            int | float,
            list,
            list[int],
            list[int | float],
            dict,
            dict[str, Any],
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

        # Non-object types get wrapped
        schema_type = base_schema.get("type")
        is_object_type = schema_type == "object"

        if not is_object_type:
            # Non-object types get wrapped
            expected_schema = {
                "type": "object",
                "properties": {"result": {**base_schema, "title": "Result"}},
                "required": ["result"],
                "title": "_WrappedResult",
                "x-fastmcp-wrap-result": True,
            }
            assert tool.output_schema == expected_schema
        else:
            # Object types remain unwrapped
            assert tool.output_schema == base_schema

    @pytest.mark.parametrize(
        "annotation",
        [
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

        expected_schema = {
            "type": "object",
            "properties": {"result": {**base_schema, "title": "Result"}},
            "required": ["result"],
            "title": "_WrappedResult",
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

    async def test_none_return_annotation(self):
        def func() -> None:
            pass

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    async def test_any_return_annotation(self):
        def func() -> Any:
            return 1

        tool = Tool.from_function(func)
        assert tool.output_schema is None

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
        expected_schema = compress_schema(TypeAdapter(Person).json_schema())
        assert tool.output_schema == expected_schema

    async def test_base_model_return_annotation(self):
        class Person(BaseModel):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        expected_schema = compress_schema(TypeAdapter(Person).json_schema())
        assert tool.output_schema == expected_schema

    async def test_typeddict_return_annotation(self):
        class Person(TypedDict):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        expected_schema = compress_schema(TypeAdapter(Person).json_schema())
        assert tool.output_schema == expected_schema

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
        custom_schema = {"type": "object", "description": "Custom schema"}

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
        custom_schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
        }

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_provided_output_schema_takes_precedence_over_no_annotation(self):
        """Test that provided output_schema takes precedence over None schema from no annotation."""

        def func():
            return "hello"

        # Provide a custom output schema even though there's no return annotation
        custom_schema = {
            "type": "object",
            "properties": {"value": {"type": "number", "minimum": 0}},
        }

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
        custom_schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}}

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
        custom_schema = {
            "type": "object",
            "properties": {"numbers": {"type": "array", "items": {"type": "number"}}},
        }

        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

    async def test_output_schema_false_allows_automatic_structured_content(self):
        """Test that output_schema=False still allows automatic structured content for dict-like objects."""

        def func() -> dict[str, str]:
            return {"message": "Hello, world!"}

        tool = Tool.from_function(func, output_schema=False)
        assert tool.output_schema is None

        result = await tool.run({})
        # Dict objects automatically become structured content even without schema
        assert result.structured_content == {"message": "Hello, world!"}
        assert len(result.content) == 1
        assert result.content[0].text == '{"message":"Hello, world!"}'  # type: ignore[attr-defined]

    async def test_output_schema_none_disables_structured_content(self):
        """Test that output_schema=None explicitly disables structured content."""

        def func() -> int:
            return 42

        tool = Tool.from_function(func, output_schema=None)
        assert tool.output_schema is None

        result = await tool.run({})
        assert result.structured_content is None
        assert len(result.content) == 1
        assert result.content[0].text == "42"  # type: ignore[attr-defined]

    async def test_output_schema_inferred_when_not_specified(self):
        """Test that output schema is inferred when not explicitly specified."""

        def func() -> int:
            return 42

        # Don't specify output_schema - should infer and wrap
        tool = Tool.from_function(func)
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "integer", "title": "Result"}},
            "required": ["result"],
            "title": "_WrappedResult",
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

        result = await tool.run({})
        assert result.structured_content == {"result": 42}

    async def test_explicit_object_schema_with_dict_return(self):
        """Test that explicit object schemas work when function returns a dict."""

        def func() -> dict[str, int]:
            return {"value": 42}

        # Provide explicit object schema
        explicit_schema = {
            "type": "object",
            "properties": {"value": {"type": "integer", "minimum": 0}},
        }
        tool = Tool.from_function(func, output_schema=explicit_schema)
        assert tool.output_schema == explicit_schema  # Schema not wrapped
        assert tool.output_schema and "x-fastmcp-wrap-result" not in tool.output_schema

        result = await tool.run({})
        # Dict result with object schema is used directly
        assert result.structured_content == {"value": 42}
        assert result.content[0].text == '{"value":42}'  # type: ignore[attr-defined]

    async def test_explicit_object_schema_with_non_dict_return_fails(self):
        """Test that explicit object schemas fail when function returns non-dict."""

        def func() -> int:
            return 42

        # Provide explicit object schema but return non-dict
        explicit_schema = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        }
        tool = Tool.from_function(func, output_schema=explicit_schema)

        # Should fail because int is not dict-compatible with object schema
        with pytest.raises(ValueError, match="structured_content must be a dict"):
            await tool.run({})

    async def test_object_output_schema_not_wrapped(self):
        """Test that object-type output schemas are never wrapped."""

        def func() -> dict[str, int]:
            return {"value": 42}

        # Object schemas should never be wrapped, even when inferred
        tool = Tool.from_function(func)
        expected_schema = TypeAdapter(dict[str, int]).json_schema()
        assert tool.output_schema == expected_schema  # Not wrapped
        assert tool.output_schema and "x-fastmcp-wrap-result" not in tool.output_schema

        result = await tool.run({})
        assert result.structured_content == {"value": 42}  # Direct value

    async def test_structured_content_interaction_with_wrapping(self):
        """Test that structured content works correctly with schema wrapping."""

        def func() -> str:
            return "hello"

        # Inferred schema should wrap string type
        tool = Tool.from_function(func)
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "string", "title": "Result"}},
            "required": ["result"],
            "title": "_WrappedResult",
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

        result = await tool.run({})
        # Unstructured content
        assert len(result.content) == 1
        assert result.content[0].text == "hello"  # type: ignore[attr-defined]
        # Structured content should be wrapped
        assert result.structured_content == {"result": "hello"}

    async def test_structured_content_with_explicit_object_schema(self):
        """Test structured content with explicit object schema."""

        def func() -> dict[str, str]:
            return {"greeting": "hello"}

        # Provide explicit object schema
        explicit_schema = {
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "required": ["greeting"],
        }
        tool = Tool.from_function(func, output_schema=explicit_schema)
        assert tool.output_schema == explicit_schema

        result = await tool.run({})
        # Should use direct value since explicit schema doesn't have wrap marker
        assert result.structured_content == {"greeting": "hello"}

    async def test_structured_content_with_custom_wrapper_schema(self):
        """Test structured content with custom schema that includes wrap marker."""

        def func() -> str:
            return "world"

        # Custom schema with wrap marker
        custom_schema = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "x-fastmcp-wrap-result": True,
        }
        tool = Tool.from_function(func, output_schema=custom_schema)
        assert tool.output_schema == custom_schema

        result = await tool.run({})
        # Should wrap with "result" key due to wrap marker
        assert result.structured_content == {"result": "world"}

    async def test_none_vs_false_output_schema_behavior(self):
        """Test the difference between None and False for output_schema."""

        def func() -> int:
            return 123

        # None should disable
        tool_none = Tool.from_function(func, output_schema=None)
        assert tool_none.output_schema is None

        # False should also disable
        tool_false = Tool.from_function(func, output_schema=False)
        assert tool_false.output_schema is None

        # Both should have same behavior
        result_none = await tool_none.run({})
        result_false = await tool_false.run({})

        assert result_none.structured_content is None
        assert result_false.structured_content is None
        assert result_none.content[0].text == result_false.content[0].text == "123"  # type: ignore[attr-defined]

    async def test_non_object_output_schema_raises_error(self):
        """Test that providing a non-object output schema raises a ValueError."""

        def func() -> int:
            return 42

        # Test various non-object schemas that should raise errors
        non_object_schemas = [
            {"type": "string"},
            {"type": "integer", "minimum": 0},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "array", "items": {"type": "string"}},
        ]

        for schema in non_object_schemas:
            with pytest.raises(
                ValueError, match='Output schemas must have "type" set to "object"'
            ):
                Tool.from_function(func, output_schema=schema)


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
        assert result[0].text == '{"a":1,"b":2}'

    def test_list_of_basic_types(self):
        """Test that a list of basic types is converted to a single TextContent."""
        result = _convert_to_content([1, "two", {"c": 3}])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '[1,"two",{"c":3}]'

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
        assert text_item.text == '[{"a":1}]'

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
        assert text_item.text == '[[{"a":1},{"b":2}]]'

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
        assert text_item.text == '[{"a":1}]'

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
        assert text_item.text == '[{"a":1}]'

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

        def custom_serializer_that_fails(data):
            raise ValueError("Serialization failed")

        with caplog_for_fastmcp(caplog):
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
        assert result[0].text == '[1,"two",{"c":3}]'

        content1 = TextContent(type="text", text="hello")
        result = _convert_to_content([1, content1], _process_as_single_item=True)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)

        assert json.loads(result[0].text) == [
            1,
            {"type": "text", "text": "hello", "annotations": None, "_meta": None},
        ]

    def test_single_element_list_preserves_structure(self):
        """Test that single-element lists preserve their list structure."""

        # Test with a single integer
        result = _convert_to_content([1])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "[1]"  # Should be "[1]", not "1"

        # Test with a single string
        result = _convert_to_content(["hello"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '["hello"]'  # Should be ["hello"], not "hello"

        # Test with a single dict
        result = _convert_to_content([{"a": 1}])
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '[{"a":1}]'  # Should be wrapped in a list


class TestAutomaticStructuredContent:
    """Tests for automatic structured content generation based on return types."""

    async def test_dict_return_creates_structured_content_without_schema(self):
        """Test that dict returns automatically create structured content even without output schema."""

        def get_user_data(user_id: str) -> dict:
            return {"name": "Alice", "age": 30, "active": True}

        # No explicit output schema provided
        tool = Tool.from_function(get_user_data)

        result = await tool.run({"user_id": "123"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.structured_content == {"name": "Alice", "age": 30, "active": True}

    async def test_dataclass_return_creates_structured_content_without_schema(self):
        """Test that dataclass returns automatically create structured content even without output schema."""

        @dataclass
        class UserProfile:
            name: str
            age: int
            email: str

        def get_profile(user_id: str) -> UserProfile:
            return UserProfile(name="Bob", age=25, email="bob@example.com")

        # No explicit output schema, but dataclass should still create structured content
        tool = Tool.from_function(get_profile, output_schema=False)

        result = await tool.run({"user_id": "456"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        # Dataclass should serialize to dict
        assert result.structured_content == {
            "name": "Bob",
            "age": 25,
            "email": "bob@example.com",
        }

    async def test_pydantic_model_return_creates_structured_content_without_schema(
        self,
    ):
        """Test that Pydantic model returns automatically create structured content even without output schema."""

        class UserData(BaseModel):
            username: str
            score: int
            verified: bool

        def get_user_stats(user_id: str) -> UserData:
            return UserData(username="charlie", score=100, verified=True)

        # Explicitly disable output schema to test automatic structured content
        tool = Tool.from_function(get_user_stats, output_schema=False)

        result = await tool.run({"user_id": "789"})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        # Pydantic model should serialize to dict
        assert result.structured_content == {
            "username": "charlie",
            "score": 100,
            "verified": True,
        }

    async def test_int_return_no_structured_content_without_schema(self):
        """Test that int returns don't create structured content without output schema."""

        def calculate_sum(a: int, b: int):
            """No return annotation."""
            return a + b

        # No output schema
        tool = Tool.from_function(calculate_sum)

        result = await tool.run({"a": 5, "b": 3})

        # Should only have content, no structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "8"
        assert result.structured_content is None

    async def test_str_return_no_structured_content_without_schema(self):
        """Test that str returns don't create structured content without output schema."""

        def get_greeting(name: str):
            """No return annotation."""
            return f"Hello, {name}!"

        # No output schema
        tool = Tool.from_function(get_greeting)

        result = await tool.run({"name": "World"})

        # Should only have content, no structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Hello, World!"
        assert result.structured_content is None

    async def test_list_return_no_structured_content_without_schema(self):
        """Test that list returns don't create structured content without output schema."""

        def get_numbers():
            """No return annotation."""
            return [1, 2, 3, 4, 5]

        # No output schema
        tool = Tool.from_function(get_numbers)

        result = await tool.run({})

        # Should only have content, no structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.structured_content is None

    async def test_int_return_with_schema_creates_structured_content(self):
        """Test that int returns DO create structured content when there's an output schema."""

        def calculate_sum(a: int, b: int) -> int:
            """With return annotation."""
            return a + b

        # Output schema should be auto-generated from annotation
        tool = Tool.from_function(calculate_sum)
        assert tool.output_schema is not None

        result = await tool.run({"a": 5, "b": 3})

        # Should have both content and structured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "8"
        assert result.structured_content == {"result": 8}

    async def test_client_automatic_deserialization_with_dict_result(self):
        """Test that clients automatically deserialize dict results from structured content."""
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @mcp.tool
        def get_user_info(user_id: str) -> dict:
            return {"name": "Alice", "age": 30, "active": True}

        async with Client(mcp) as client:
            result = await client.call_tool("get_user_info", {"user_id": "123"})

            # Client should provide the deserialized data
            assert result.data == {"name": "Alice", "age": 30, "active": True}
            assert result.structured_content == {
                "name": "Alice",
                "age": 30,
                "active": True,
            }
            assert len(result.content) == 1

    async def test_client_automatic_deserialization_with_dataclass_result(self):
        """Test that clients automatically deserialize dataclass results from structured content."""
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @dataclass
        class UserProfile:
            name: str
            age: int
            verified: bool

        @mcp.tool
        def get_profile(user_id: str) -> UserProfile:
            return UserProfile(name="Bob", age=25, verified=True)

        async with Client(mcp) as client:
            result = await client.call_tool("get_profile", {"user_id": "456"})

            # Client should deserialize back to a dataclass (type name preserved with new compression)
            assert result.data.__class__.__name__ == "UserProfile"
            assert result.data.name == "Bob"
            assert result.data.age == 25
            assert result.data.verified is True


class TestUnionReturnTypes:
    """Tests for tools with union return types."""

    async def test_dataclass_union_string_works(self):
        """Test that union of dataclass and string works correctly."""

        @dataclass
        class Data:
            value: int

        def get_data(return_error: bool) -> Data | str:
            if return_error:
                return "error occurred"
            return Data(value=42)

        tool = Tool.from_function(get_data)

        # Test returning dataclass
        result1 = await tool.run({"return_error": False})
        assert result1.structured_content == {"result": {"value": 42}}

        # Test returning string
        result2 = await tool.run({"return_error": True})
        assert result2.structured_content == {"result": "error occurred"}


class TestSerializationAlias:
    """Tests for Pydantic field serialization alias support in tool output schemas."""

    def test_output_schema_respects_serialization_alias(self):
        """Test that Tool.from_function generates output schema using serialization alias."""
        from pydantic import AliasChoices, BaseModel, Field

        class Component(BaseModel):
            """Model with multiple validation aliases but specific serialization alias."""

            component_id: str = Field(
                validation_alias=AliasChoices("id", "componentId"),
                serialization_alias="componentId",
                description="The ID of the component",
            )

        async def get_component(
            component_id: str,
        ) -> Annotated[Component, Field(description="The component.")]:
            # API returns data with 'id' field
            api_data = {"id": component_id}
            return Component.model_validate(api_data)

        tool = Tool.from_function(get_component, name="get-component")

        # The output schema should use the serialization alias 'componentId'
        # not the first validation alias 'id'
        assert tool.output_schema is not None

        # Check the wrapped result schema
        assert "properties" in tool.output_schema
        assert "result" in tool.output_schema["properties"]
        assert "$defs" in tool.output_schema

        # Find the Component definition
        component_def = list(tool.output_schema["$defs"].values())[0]

        # Should have 'componentId' not 'id' in properties
        assert "componentId" in component_def["properties"]
        assert "id" not in component_def["properties"]

        # Should require 'componentId' not 'id'
        assert "componentId" in component_def["required"]
        assert "id" not in component_def.get("required", [])

    async def test_tool_execution_with_serialization_alias(self):
        """Test that tool execution works correctly with serialization aliases."""
        from pydantic import AliasChoices, BaseModel, Field

        from fastmcp import Client, FastMCP

        class Component(BaseModel):
            """Model with multiple validation aliases but specific serialization alias."""

            component_id: str = Field(
                validation_alias=AliasChoices("id", "componentId"),
                serialization_alias="componentId",
                description="The ID of the component",
            )

        mcp = FastMCP("TestServer")

        @mcp.tool
        async def get_component(
            component_id: str,
        ) -> Annotated[Component, Field(description="The component.")]:
            # API returns data with 'id' field
            api_data = {"id": component_id}
            return Component.model_validate(api_data)

        async with Client(mcp) as client:
            # Execute the tool - this should work without validation errors
            result = await client.call_tool(
                "get_component", {"component_id": "test123"}
            )

            # The result should contain the serialized form with 'componentId'
            assert result.structured_content is not None
            assert result.structured_content["result"]["componentId"] == "test123"
            assert "id" not in result.structured_content["result"]


class TestToolTitle:
    """Tests for tool title functionality."""

    def test_tool_with_title(self):
        """Test that tools can have titles and they appear in MCP conversion."""

        def calculate(x: int, y: int) -> int:
            """Calculate the sum of two numbers."""
            return x + y

        tool = Tool.from_function(
            calculate,
            name="calc",
            title="Advanced Calculator Tool",
            description="Custom description",
        )

        assert tool.name == "calc"
        assert tool.title == "Advanced Calculator Tool"
        assert tool.description == "Custom description"

        # Test MCP conversion includes title
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.name == "calc"
        assert (
            hasattr(mcp_tool, "title") and mcp_tool.title == "Advanced Calculator Tool"
        )

    def test_tool_without_title(self):
        """Test that tools without titles use name as display name."""

        def multiply(a: int, b: int) -> int:
            return a * b

        tool = Tool.from_function(multiply)

        assert tool.name == "multiply"
        assert tool.title is None

        # Test MCP conversion doesn't include title when None
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.name == "multiply"
        assert not hasattr(mcp_tool, "title") or mcp_tool.title is None

    def test_tool_title_priority(self):
        """Test that explicit title takes priority over annotations.title."""
        from mcp.types import ToolAnnotations

        def divide(x: int, y: int) -> float:
            """Divide two numbers."""
            return x / y

        # Test with both explicit title and annotations.title
        annotations = ToolAnnotations(title="Annotation Title")
        tool = Tool.from_function(
            divide,
            name="div",
            title="Explicit Title",
            annotations=annotations,
        )

        assert tool.title == "Explicit Title"
        assert tool.annotations is not None
        assert tool.annotations.title == "Annotation Title"

        # Explicit title should take priority
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.title == "Explicit Title"

    def test_tool_annotations_title_fallback(self):
        """Test that annotations.title is used when no explicit title is provided."""
        from mcp.types import ToolAnnotations

        def modulo(x: int, y: int) -> int:
            """Get modulo of two numbers."""
            return x % y

        # Test with only annotations.title (no explicit title)
        annotations = ToolAnnotations(title="Annotation Title")
        tool = Tool.from_function(
            modulo,
            name="mod",
            annotations=annotations,
        )

        assert tool.title is None
        assert tool.annotations is not None
        assert tool.annotations.title == "Annotation Title"

        # Should fall back to annotations.title
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.title == "Annotation Title"
