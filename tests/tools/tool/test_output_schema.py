from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from inline_snapshot import snapshot
from mcp.types import AudioContent, EmbeddedResource, ImageContent, TextContent
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp.tools.tool import Tool
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import Audio, File, Image


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
        def func() -> annotation:
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
                "properties": {"result": base_schema},
                "required": ["result"],
                "x-fastmcp-wrap-result": True,
            }
            assert tool.output_schema == expected_schema
            # # Note: Parameterized test - keeping original assertion for multiple parameter values
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
        def func() -> annotation:
            return 1

        tool = Tool.from_function(func)

        base_schema = TypeAdapter(annotation).json_schema()
        expected_schema = {
            "type": "object",
            "properties": {"result": base_schema},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert tool.output_schema == expected_schema

    async def test_none_return_annotation(self):
        def func() -> None:
            pass

        tool = Tool.from_function(func)
        assert tool.output_schema is None

    async def test_any_return_annotation(self):
        from typing import Any

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
        def func() -> annotation:
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
        expected_schema = compress_schema(
            TypeAdapter(Person).json_schema(), prune_titles=True
        )
        assert tool.output_schema == expected_schema

    async def test_base_model_return_annotation(self):
        class Person(BaseModel):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)

        assert tool.output_schema == snapshot(
            {
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name", "age"],
                "type": "object",
            }
        )

    async def test_typeddict_return_annotation(self):
        class Person(TypedDict):
            name: str
            age: int

        def func() -> Person:
            return Person(name="John", age=30)

        tool = Tool.from_function(func)
        assert tool.output_schema == snapshot(
            {
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name", "age"],
                "type": "object",
            }
        )

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

        tool = Tool.from_function(func, output_schema=None)
        assert tool.output_schema is None

        result = await tool.run({})
        # Dict objects automatically become structured content even without schema
        assert result.structured_content == {"message": "Hello, world!"}
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == '{"message":"Hello, world!"}'

    async def test_output_schema_none_disables_structured_content(self):
        """Test that output_schema=None explicitly disables structured content."""

        def func() -> int:
            return 42

        tool = Tool.from_function(func, output_schema=None)
        assert tool.output_schema is None

        result = await tool.run({})
        assert result.structured_content is None
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "42"

    async def test_output_schema_inferred_when_not_specified(self):
        """Test that output schema is inferred when not explicitly specified."""

        def func() -> int:
            return 42

        # Don't specify output_schema - should infer and wrap
        tool = Tool.from_function(func)
        assert tool.output_schema == snapshot(
            {
                "properties": {"result": {"type": "integer"}},
                "required": ["result"],
                "type": "object",
                "x-fastmcp-wrap-result": True,
            }
        )

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
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == '{"value":42}'

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
        assert tool.output_schema == snapshot(
            {
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "type": "object",
                "x-fastmcp-wrap-result": True,
            }
        )

        result = await tool.run({})
        # Unstructured content
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "hello"
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

        # Default (NotSet) should infer from return type
        tool_default = Tool.from_function(func)
        assert (
            tool_default.output_schema is not None
        )  # Should infer schema from dict return type

        # Different behavior: None vs inferred
        result_none = await tool_none.run({})
        result_default = await tool_default.run({})

        # None should still try fallback generation but fail for non-dict
        assert result_none.structured_content is None  # Fallback fails for int
        # Default should use proper schema and wrap the result
        assert result_default.structured_content == {
            "result": 123
        }  # Schema-based generation with wrapping
        assert isinstance(result_none.content[0], TextContent)
        assert isinstance(result_default.content[0], TextContent)
        assert result_none.content[0].text == result_default.content[0].text == "123"

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
                ValueError, match="Output schemas must represent object types"
            ):
                Tool.from_function(func, output_schema=schema)
