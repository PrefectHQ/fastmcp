from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from dirty_equals import HasName
from inline_snapshot import snapshot
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
    ToolExecution,
)
from pydantic import AnyUrl, BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp.tools.tool import Tool, ToolResult, _convert_to_content
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

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "fn": HasName("add"),
                "task_config": {"mode": "forbidden"},
            }
        )

    def test_meta_parameter(self):
        """Test that meta parameter is properly handled."""

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        meta_data = {"version": "1.0", "author": "test"}
        tool = Tool.from_function(multiply, meta=meta_data)

        assert tool.meta == meta_data
        mcp_tool = tool.to_mcp_tool()

        # MCP tool includes fastmcp meta, so check that our meta is included
        assert mcp_tool.meta is not None
        assert meta_data.items() <= mcp_tool.meta.items()

    async def test_async_function(self):
        """Test registering and running an async function."""

        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f"Data from {url}"

        tool = Tool.from_function(fetch_data)

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "fetch_data",
                "description": "Fetch data from URL.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "fn": HasName("fetch_data"),
                "task_config": {"mode": "forbidden"},
            }
        )

    def test_callable_object(self):
        class Adder:
            """Adds two numbers."""

            def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "Adder",
                "description": "Adds two numbers.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {"mode": "forbidden"},
            }
        )

    def test_async_callable_object(self):
        class Adder:
            """Adds two numbers."""

            async def __call__(self, x: int, y: int) -> int:
                """ignore this"""
                return x + y

        tool = Tool.from_function(Adder())

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "Adder",
                "description": "Adds two numbers.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {"mode": "forbidden"},
            }
        )

    def test_pydantic_model_function(self):
        """Test registering a function that takes a Pydantic model."""

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput, flag: bool) -> dict:
            """Create a new user."""
            return {"id": 1, **user.model_dump()}

        tool = Tool.from_function(create_user)

        assert tool.model_dump(exclude_none=True) == snapshot(
            {
                "name": "create_user",
                "description": "Create a new user.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "$defs": {
                        "UserInput": {
                            "properties": {
                                "name": {"type": "string"},
                                "age": {"type": "integer"},
                            },
                            "required": ["name", "age"],
                            "type": "object",
                        }
                    },
                    "properties": {
                        "user": {"$ref": "#/$defs/UserInput"},
                        "flag": {"type": "boolean"},
                    },
                    "required": ["user", "flag"],
                    "type": "object",
                },
                "output_schema": {"additionalProperties": True, "type": "object"},
                "fn": HasName("create_user"),
                "task_config": {"mode": "forbidden"},
            }
        )

    async def test_tool_with_image_return(self):
        def image_tool(data: bytes) -> Image:
            return Image(data=data)

        tool = Tool.from_function(image_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result = await tool.run({"data": "test.png"})
        assert isinstance(result.content[0], ImageContent)

    async def test_tool_with_audio_return(self):
        def audio_tool(data: bytes) -> Audio:
            return Audio(data=data)

        tool = Tool.from_function(audio_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result = await tool.run({"data": "test.wav"})
        assert isinstance(result.content[0], AudioContent)

    async def test_tool_with_file_return(self):
        def file_tool(data: bytes) -> File:
            return File(data=data, format="octet-stream")

        tool = Tool.from_function(file_tool)
        assert tool.parameters["properties"]["data"]["type"] == "string"
        assert tool.output_schema is None

        result: ToolResult = await tool.run({"data": "test.bin"})
        assert result.content[0].model_dump(exclude_none=True) == snapshot(
            {
                "type": "resource",
                "resource": {
                    "uri": AnyUrl("file:///resource.octet-stream"),
                    "mimeType": "application/octet-stream",
                    "blob": "dGVzdC5iaW4=",
                },
            }
        )

    def test_non_callable_fn(self):
        with pytest.raises(TypeError, match="not a callable object"):
            Tool.from_function(1)  # type: ignore

    def test_lambda(self):
        tool = Tool.from_function(lambda x: x, name="my_tool")
        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "my_tool",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {"x": {"title": "X"}},
                    "required": ["x"],
                    "type": "object",
                },
                "task_config": {"mode": "forbidden"},
            }
        )

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

        assert tool.model_dump(
            exclude_none=True, exclude={"output_schema", "fn"}
        ) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {
                        "_a": {"type": "integer"},
                        "_b": {"type": "integer"},
                    },
                    "required": ["_a", "_b"],
                    "type": "object",
                },
                "task_config": {"mode": "forbidden"},
            }
        )

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
        assert "self" not in tool.parameters["properties"]

        assert tool.model_dump(exclude_none=True, exclude={"fn"}) == snapshot(
            {
                "name": "add",
                "description": "Add two numbers.",
                "tags": set(),
                "enabled": True,
                "parameters": {
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                    "type": "object",
                },
                "output_schema": {
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                },
                "task_config": {"mode": "forbidden"},
            }
        )

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
        def func() -> annotation:  # type: ignore
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
        assert result_none.content[0].text == result_default.content[0].text == "123"  # type: ignore[attr-defined]

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


class SampleModel(BaseModel):
    x: int
    y: str


class TestConvertResultToContent:
    """Tests for the _convert_to_content helper function."""

    @pytest.mark.parametrize(
        argnames=("result", "expected"),
        argvalues=[
            (True, "true"),
            ("hello", "hello"),
            (123, "123"),
            (123.45, "123.45"),
            ({"key": "value"}, '{"key":"value"}'),
            (
                SampleModel(x=1, y="hello"),
                '{"x":1,"y":"hello"}',
            ),
        ],
        ids=[
            "boolean",
            "string",
            "integer",
            "float",
            "object",
            "basemodel",
        ],
    )
    def test_convert_singular(self, result, expected):
        """Test that a single item is converted to a TextContent."""
        converted = _convert_to_content(result)
        assert converted == [TextContent(type="text", text=expected)]

    @pytest.mark.parametrize(
        argnames=("result", "expected_text"),
        argvalues=[
            ([None], "[null]"),
            ([None, None], "[null,null]"),
            ([True], "[true]"),
            ([True, False], "[true,false]"),
            (["hello"], '["hello"]'),
            (["hello", "world"], '["hello","world"]'),
            ([123], "[123]"),
            ([123, 456], "[123,456]"),
            ([123.45], "[123.45]"),
            ([123.45, 456.78], "[123.45,456.78]"),
            ([{"key": "value"}], '[{"key":"value"}]'),
            (
                [{"key": "value"}, {"key2": "value2"}],
                '[{"key":"value"},{"key2":"value2"}]',
            ),
            ([SampleModel(x=1, y="hello")], '[{"x":1,"y":"hello"}]'),
            (
                [SampleModel(x=1, y="hello"), SampleModel(x=2, y="world")],
                '[{"x":1,"y":"hello"},{"x":2,"y":"world"}]',
            ),
            ([1, "two", None, {"c": 3}, False], '[1,"two",null,{"c":3},false]'),
        ],
        ids=[
            "none",
            "none_many",
            "boolean",
            "boolean_many",
            "string",
            "string_many",
            "integer",
            "integer_many",
            "float",
            "float_many",
            "object",
            "object_many",
            "basemodel",
            "basemodel_many",
            "mixed",
        ],
    )
    def test_convert_list(self, result, expected_text):
        """Test that a list is converted to a TextContent."""
        converted = _convert_to_content(result)
        assert converted == [TextContent(type="text", text=expected_text)]

    @pytest.mark.parametrize(
        argnames="content_block",
        argvalues=[
            (TextContent(type="text", text="hello")),
            (ImageContent(type="image", data="fakeimagedata", mimeType="image/png")),
            (AudioContent(type="audio", data="fakeaudiodata", mimeType="audio/mpeg")),
            (
                ResourceLink(
                    type="resource_link",
                    name="test resource",
                    uri=AnyUrl("resource://test"),
                )
            ),
            (
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("resource://test"),
                        mimeType="text/plain",
                        text="resource content",
                    ),
                )
            ),
        ],
        ids=["text", "image", "audio", "resource link", "embedded resource"],
    )
    def test_convert_content_block(self, content_block):
        converted = _convert_to_content(content_block)
        assert converted == [content_block]

        converted = _convert_to_content([content_block, content_block])
        assert converted == [content_block, content_block]

    @pytest.mark.parametrize(
        argnames=("result", "expected"),
        argvalues=[
            (
                Image(data=b"fakeimagedata"),
                [
                    ImageContent(
                        type="image", data="ZmFrZWltYWdlZGF0YQ==", mimeType="image/png"
                    )
                ],
            ),
            (
                Audio(data=b"fakeaudiodata"),
                [
                    AudioContent(
                        type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                    )
                ],
            ),
            (
                File(data=b"filedata", format="octet-stream"),
                [
                    EmbeddedResource(
                        type="resource",
                        resource=BlobResourceContents(
                            uri=AnyUrl("file:///resource.octet-stream"),
                            blob="ZmlsZWRhdGE=",
                            mimeType="application/octet-stream",
                        ),
                    )
                ],
            ),
        ],
        ids=["image", "audio", "file"],
    )
    def test_convert_helpers(self, result, expected):
        converted = _convert_to_content(result)
        assert converted == expected

        converted = _convert_to_content([result, result])
        assert converted == expected * 2

    def test_convert_mixed_content(self):
        result = [
            "hello",
            123,
            123.45,
            {"key": "value"},
            SampleModel(x=1, y="hello"),
            Image(data=b"fakeimagedata"),
            Audio(data=b"fakeaudiodata"),
            ResourceLink(
                type="resource_link",
                name="test resource",
                uri=AnyUrl("resource://test"),
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=AnyUrl("resource://test"),
                    mimeType="text/plain",
                    text="resource content",
                ),
            ),
        ]

        converted = _convert_to_content(result)

        assert converted == snapshot(
            [
                TextContent(type="text", text="hello"),
                TextContent(type="text", text="123"),
                TextContent(type="text", text="123.45"),
                TextContent(type="text", text='{"key":"value"}'),
                TextContent(type="text", text='{"x":1,"y":"hello"}'),
                ImageContent(
                    type="image", data="ZmFrZWltYWdlZGF0YQ==", mimeType="image/png"
                ),
                AudioContent(
                    type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                ),
                ResourceLink(
                    name="test resource",
                    uri=AnyUrl("resource://test"),
                    type="resource_link",
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("resource://test"),
                        mimeType="text/plain",
                        text="resource content",
                    ),
                ),
            ]
        )

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

    def test_custom_serializer(self):
        """Test that a custom serializer is used for non-MCP types."""

        def custom_serializer(data):
            return f"Serialized: {data}"

        result = _convert_to_content({"a": 1}, serializer=custom_serializer)

        assert result == snapshot(
            [TextContent(type="text", text="Serialized: {'a': 1}")]
        )

    def test_custom_serializer_error_fallback(self, caplog):
        """Test that if a custom serializer fails, it falls back to the default."""

        def custom_serializer_that_fails(data):
            raise ValueError("Serialization failed")

        with caplog_for_fastmcp(caplog):
            result = _convert_to_content(
                {"a": 1}, serializer=custom_serializer_that_fails
            )

        assert isinstance(result, list)
        assert result == snapshot([TextContent(type="text", text='{"a":1}')])

        assert "Error serializing tool result" in caplog.text


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
        tool = Tool.from_function(get_profile, output_schema=None)

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

        # Explicitly set output schema to None to test automatic structured content
        tool = Tool.from_function(get_user_stats, output_schema=None)

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

    async def test_self_referencing_dataclass_not_wrapped(self):
        """Test that self-referencing dataclasses are not wrapped in result field."""

        @dataclass
        class ReturnThing:
            value: int
            stuff: list["ReturnThing"]

        def return_things() -> ReturnThing:
            return ReturnThing(value=123, stuff=[ReturnThing(value=456, stuff=[])])

        tool = Tool.from_function(return_things)

        result = await tool.run({})

        # Should have structured content without wrapping
        assert result.structured_content is not None
        # Should NOT be wrapped in "result" field
        assert "result" not in result.structured_content
        # Should have the actual data directly
        assert result.structured_content == {
            "value": 123,
            "stuff": [{"value": 456, "stuff": []}],
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

        assert result.structured_content is None
        assert result.content == snapshot(
            [TextContent(type="text", text="[1,2,3,4,5]")]
        )

    async def test_audio_return_creates_no_structured_content(self):
        """Test that audio returns don't create structured content."""

        def get_audio() -> AudioContent:
            """No return annotation."""
            return Audio(data=b"fakeaudiodata").to_audio_content()

        # No output schema
        tool = Tool.from_function(get_audio)

        result = await tool.run({})

        assert result.content == snapshot(
            [
                AudioContent(
                    type="audio", data="ZmFrZWF1ZGlvZGF0YQ==", mimeType="audio/wav"
                )
            ]
        )
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

            # Client should deserialize back to a dataclass (but type name is lost with title pruning)
            assert result.data.__class__.__name__ == "Root"
            assert result.data.name == "Bob"
            assert result.data.age == 25
            assert result.data.verified is True


class TestToolResultCasting:
    @pytest.fixture
    async def client(self):
        from fastmcp import FastMCP
        from fastmcp.client import Client

        mcp = FastMCP()

        @mcp.tool
        def test_tool(
            unstructured: str | None = None,
            structured: dict[str, Any] | None = None,
            meta: dict[str, Any] | None = None,
        ):
            return ToolResult(
                content=unstructured,
                structured_content=structured,
                meta=meta,
            )

        async with Client(mcp) as client:
            yield client

    async def test_only_unstructured_content(self, client):
        result = await client.call_tool("test_tool", {"unstructured": "test data"})

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content is None
        assert result.meta is None

    async def test_neither_unstructured_or_structured_content(self, client):
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            await client.call_tool("test_tool", {})

    async def test_structured_and_unstructured_content(self, client):
        result = await client.call_tool(
            "test_tool",
            {"unstructured": "test data", "structured": {"data_type": "test"}},
        )

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content == {"data_type": "test"}
        assert result.meta is None

    async def test_structured_unstructured_and_meta_content(self, client):
        result = await client.call_tool(
            "test_tool",
            {
                "unstructured": "test data",
                "structured": {"data_type": "test"},
                "meta": {"some": "metadata"},
            },
        )

        assert result.content[0].type == "text"
        assert result.content[0].text == "test data"
        assert result.structured_content == {"data_type": "test"}
        assert result.meta == {"some": "metadata"}


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

        # For object types, the schema may use $ref at root (self-referencing types)
        # or have properties directly. Check both cases.
        if "$ref" in tool.output_schema:
            # Schema uses $ref - resolve to get the actual definition
            assert "$defs" in tool.output_schema
            ref_path = tool.output_schema["$ref"].replace("#/$defs/", "")
            component_def = tool.output_schema["$defs"][ref_path]
        else:
            # Schema has properties directly (wrapped case)
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
            # Object types may be wrapped in "result" or not, depending on schema structure
            if "result" in result.structured_content:
                component_data = result.structured_content["result"]
            else:
                component_data = result.structured_content
            assert component_data["componentId"] == "test123"
            assert "id" not in component_data


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


class TestToolNameValidation:
    """Tests for tool name validation per MCP specification (SEP-986)."""

    @pytest.fixture
    def caplog_for_mcp_validation(self, caplog):
        """Capture logs from the MCP SDK's tool name validation logger."""
        import logging

        caplog.set_level(logging.WARNING)
        logger = logging.getLogger("mcp.shared.tool_name_validation")
        original_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(caplog.handler)
        try:
            yield caplog
        finally:
            logger.removeHandler(caplog.handler)
            logger.setLevel(original_level)

    @pytest.mark.parametrize(
        "name",
        [
            "valid_tool",
            "valid-tool",
            "valid.tool",
            "ValidTool",
            "tool123",
            "a",
            "a" * 128,
        ],
    )
    def test_valid_tool_names_no_warnings(self, name, caplog_for_mcp_validation):
        """Valid tool names should not produce warnings."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name=name)
        assert tool.name == name
        assert "Tool name validation warning" not in caplog_for_mcp_validation.text

    def test_tool_name_with_spaces_warns(self, caplog_for_mcp_validation):
        """Tool names with spaces should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="my tool")
        assert tool.name == "my tool"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "contains spaces" in caplog_for_mcp_validation.text

    def test_tool_name_with_invalid_chars_warns(self, caplog_for_mcp_validation):
        """Tool names with invalid characters should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="tool@name!")
        assert tool.name == "tool@name!"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "invalid characters" in caplog_for_mcp_validation.text

    def test_tool_name_too_long_warns(self, caplog_for_mcp_validation):
        """Tool names exceeding 128 characters should produce a warning."""

        def fn() -> str:
            return "test"

        long_name = "a" * 129
        tool = Tool.from_function(fn, name=long_name)
        assert tool.name == long_name
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "exceeds maximum length" in caplog_for_mcp_validation.text

    def test_tool_name_with_leading_dash_warns(self, caplog_for_mcp_validation):
        """Tool names starting with dash should produce a warning."""

        def fn() -> str:
            return "test"

        tool = Tool.from_function(fn, name="-tool")
        assert tool.name == "-tool"
        assert "Tool name validation warning" in caplog_for_mcp_validation.text
        assert "starts or ends with a dash" in caplog_for_mcp_validation.text

    def test_tool_still_created_despite_warnings(self, caplog_for_mcp_validation):
        """Tools with invalid names should still be created (SHOULD not MUST)."""

        def add(a: int, b: int) -> int:
            return a + b

        tool = Tool.from_function(add, name="invalid tool name!")
        assert tool.name == "invalid tool name!"
        assert tool.parameters is not None
        assert "a" in tool.parameters["properties"]
        assert "b" in tool.parameters["properties"]


class TestToolExecutionField:
    """Tests for the execution field on the base Tool class."""

    def test_tool_with_execution_field(self):
        """Test that Tool can store and return execution metadata."""
        tool = Tool(
            name="my_tool",
            description="A tool with execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="optional"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "optional"

    def test_tool_without_execution_field(self):
        """Test that Tool without execution returns None."""
        tool = Tool(
            name="my_tool",
            description="A tool without execution",
            parameters={"type": "object", "properties": {}},
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is None

    def test_execution_override_takes_precedence(self):
        """Test that explicit override takes precedence over field value."""
        tool = Tool(
            name="my_tool",
            description="A tool",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="optional"),
        )

        override_execution = ToolExecution(taskSupport="required")
        mcp_tool = tool.to_mcp_tool(execution=override_execution)
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "required"

    async def test_function_tool_task_config_still_works(self):
        """FunctionTool should still derive execution from task_config."""

        async def my_fn() -> str:
            return "hello"

        tool = Tool.from_function(my_fn, task=True)
        mcp_tool = tool.to_mcp_tool()

        # FunctionTool sets execution from task_config
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "optional"

    def test_tool_execution_required_mode(self):
        """Test that Tool can store required execution mode."""
        tool = Tool(
            name="my_tool",
            description="A tool with required execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="required"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "required"

    def test_tool_execution_forbidden_mode(self):
        """Test that Tool can store forbidden execution mode."""
        tool = Tool(
            name="my_tool",
            description="A tool with forbidden execution",
            parameters={"type": "object", "properties": {}},
            execution=ToolExecution(taskSupport="forbidden"),
        )

        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None
        assert mcp_tool.execution.taskSupport == "forbidden"
