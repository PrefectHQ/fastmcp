from typing import Annotated, Any

import pytest
from dirty_equals import IsList
from inline_snapshot import snapshot
from mcp.types import TextContent
from pydantic import BaseModel, Field, TypeAdapter

from fastmcp.tools import Tool, forward
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import ToolResult
from fastmcp.tools.tool_transform import (
    ArgTransform,
    TransformedTool,
)


def get_property(tool: Tool, name: str) -> dict[str, Any]:
    return tool.parameters["properties"][name]


@pytest.fixture
def add_tool() -> FunctionTool:
    def add(
        old_x: Annotated[int, Field(description="old_x description")], old_y: int = 10
    ) -> int:
        print("running!")
        return old_x + old_y

    return Tool.from_function(add)


class TestTransformToolOutputSchema:
    """Test output schema handling in transformed tools."""

    @pytest.fixture
    def base_string_tool(self) -> FunctionTool:
        """Tool that returns a string (gets wrapped)."""

        def string_tool(x: int) -> str:
            return f"Result: {x}"

        return Tool.from_function(string_tool)

    @pytest.fixture
    def base_dict_tool(self) -> FunctionTool:
        """Tool that returns a dict (object type, not wrapped)."""

        def dict_tool(x: int) -> dict[str, int]:
            return {"value": x}

        return Tool.from_function(dict_tool)

    def test_transform_inherits_parent_output_schema(self, base_string_tool):
        """Test that transformed tool inherits parent's output schema by default."""
        new_tool = Tool.from_tool(base_string_tool)

        # Should inherit parent's wrapped string schema
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert new_tool.output_schema == expected_schema
        assert new_tool.output_schema == base_string_tool.output_schema

    def test_transform_with_explicit_output_schema_none(self, base_string_tool):
        """Test that output_schema=None sets output schema to None."""
        new_tool = Tool.from_tool(base_string_tool, output_schema=None)

        assert new_tool.output_schema is None

    async def test_transform_output_schema_none_runtime(self, base_string_tool):
        """Test runtime behavior with output_schema=None."""
        new_tool = Tool.from_tool(base_string_tool, output_schema=None)

        # Debug: check that output_schema is actually None
        assert new_tool.output_schema is None, (
            f"Expected None, got {new_tool.output_schema}"
        )

        result = await new_tool.run({"x": 5})
        # Even with output_schema=None, structured content should be generated via fallback logic
        assert result.structured_content == {"result": "Result: 5"}
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Result: 5"

    def test_transform_with_explicit_output_schema_dict(self, base_string_tool):
        """Test that explicit output schema overrides parent."""
        custom_schema = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
        }
        new_tool = Tool.from_tool(base_string_tool, output_schema=custom_schema)

        assert new_tool.output_schema == custom_schema
        assert new_tool.output_schema != base_string_tool.output_schema

    async def test_transform_explicit_schema_runtime(self, base_string_tool):
        """Test runtime behavior with explicit output schema."""
        custom_schema = {"type": "string", "minLength": 1}
        new_tool = Tool.from_tool(base_string_tool, output_schema=custom_schema)

        result = await new_tool.run({"x": 10})
        # Non-object explicit schemas disable structured content
        assert result.structured_content is None
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Result: 10"

    def test_transform_with_custom_function_inferred_schema(self, base_dict_tool):
        """Test that custom function's output schema is inferred."""

        async def custom_fn(x: int) -> str:
            result = await forward(x=x)
            assert isinstance(result.content[0], TextContent)
            return f"Custom: {result.content[0].text}"

        new_tool = Tool.from_tool(base_dict_tool, transform_fn=custom_fn)

        # Should infer string schema from custom function and wrap it
        expected_schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        assert new_tool.output_schema == expected_schema

    async def test_transform_custom_function_runtime(self, base_dict_tool):
        """Test runtime behavior with custom function that has inferred schema."""

        async def custom_fn(x: int) -> str:
            result = await forward(x=x)
            assert isinstance(result.content[0], TextContent)
            return f"Custom: {result.content[0].text}"

        new_tool = Tool.from_tool(base_dict_tool, transform_fn=custom_fn)

        result = await new_tool.run({"x": 3})
        # Should wrap string result
        assert result.structured_content == {"result": 'Custom: {"value":3}'}

    def test_transform_custom_function_fallback_to_parent(self, base_string_tool):
        """Test that custom function without output annotation falls back to parent."""

        async def custom_fn(x: int):
            # No return annotation - should fallback to parent schema
            result = await forward(x=x)
            return result

        new_tool = Tool.from_tool(base_string_tool, transform_fn=custom_fn)

        # Should use parent's schema since custom function has no annotation
        assert new_tool.output_schema == base_string_tool.output_schema

    def test_transform_custom_function_explicit_overrides(self, base_string_tool):
        """Test that explicit output_schema overrides both custom function and parent."""

        async def custom_fn(x: int) -> dict[str, str]:
            return {"custom": "value"}

        explicit_schema = {"type": "array", "items": {"type": "number"}}
        new_tool = Tool.from_tool(
            base_string_tool, transform_fn=custom_fn, output_schema=explicit_schema
        )

        # Explicit schema should win
        assert new_tool.output_schema == explicit_schema

    async def test_transform_custom_function_object_return(self, base_string_tool):
        """Test custom function returning object type."""

        async def custom_fn(x: int) -> dict[str, int]:
            await forward(x=x)
            return {"original": x, "transformed": x * 2}

        new_tool = Tool.from_tool(base_string_tool, transform_fn=custom_fn)

        # Object types should not be wrapped
        expected_schema = TypeAdapter(dict[str, int]).json_schema()
        assert new_tool.output_schema == expected_schema
        assert isinstance(new_tool.output_schema, dict)
        assert "x-fastmcp-wrap-result" not in new_tool.output_schema

        result = await new_tool.run({"x": 4})
        # Direct value, not wrapped
        assert result.structured_content == {"original": 4, "transformed": 8}

    async def test_transform_preserves_wrap_marker_behavior(self, base_string_tool):
        """Test that wrap marker behavior is preserved through transformation."""
        new_tool = Tool.from_tool(base_string_tool)

        result = await new_tool.run({"x": 7})
        # Should wrap because parent schema has wrap marker
        assert result.structured_content == {"result": "Result: 7"}
        assert isinstance(new_tool.output_schema, dict)
        assert "x-fastmcp-wrap-result" in new_tool.output_schema

    def test_transform_chained_output_schema_inheritance(self, base_string_tool):
        """Test output schema inheritance through multiple transformations."""
        # First transformation keeps parent schema
        tool1 = Tool.from_tool(base_string_tool)
        assert tool1.output_schema == base_string_tool.output_schema

        # Second transformation also inherits
        tool2 = Tool.from_tool(tool1)
        assert (
            tool2.output_schema == tool1.output_schema == base_string_tool.output_schema
        )

        # Third transformation with explicit override
        custom_schema = {"type": "number"}
        tool3 = Tool.from_tool(tool2, output_schema=custom_schema)
        assert tool3.output_schema == custom_schema
        assert tool3.output_schema != tool2.output_schema

    async def test_transform_mixed_structured_unstructured_content(
        self, base_string_tool
    ):
        """Test transformation handling of mixed content types."""

        async def custom_fn(x: int):
            # Return mixed content including ToolResult
            if x == 1:
                return ["text", {"data": x}]
            else:
                # Return ToolResult directly
                return ToolResult(
                    content=[TextContent(type="text", text=f"Custom: {x}")],
                    structured_content={"custom_value": x},
                )

        new_tool = Tool.from_tool(base_string_tool, transform_fn=custom_fn)

        # Test mixed content return
        result1 = await new_tool.run({"x": 1})
        assert result1.structured_content == {"result": ["text", {"data": 1}]}

        # Test ToolResult return
        result2 = await new_tool.run({"x": 2})
        assert result2.structured_content == {"custom_value": 2}
        assert isinstance(result2.content[0], TextContent)
        assert result2.content[0].text == "Custom: 2"

    def test_transform_output_schema_with_arg_transforms(self, base_string_tool):
        """Test that output schema works correctly with argument transformations."""

        async def custom_fn(new_x: int) -> dict[str, str]:
            result = await forward(new_x=new_x)
            assert isinstance(result.content[0], TextContent)
            return {"transformed": result.content[0].text}

        new_tool = Tool.from_tool(
            base_string_tool,
            transform_fn=custom_fn,
            transform_args={"x": ArgTransform(name="new_x")},
        )

        # Should infer object schema from custom function
        expected_schema = TypeAdapter(dict[str, str]).json_schema()
        assert new_tool.output_schema == expected_schema

    async def test_transform_output_schema_default_vs_none(self, base_string_tool):
        """Test default (NotSet) vs explicit None behavior for output_schema in transforms."""
        # Default (NotSet) should use smart fallback (inherit from parent)
        tool_default = Tool.from_tool(base_string_tool)  # default output_schema=NotSet
        assert tool_default.output_schema == base_string_tool.output_schema  # Inherits

        # None should explicitly set output_schema to None but still generate structured content via fallback
        tool_explicit_none = Tool.from_tool(base_string_tool, output_schema=None)
        assert tool_explicit_none.output_schema is None

        # Both should generate structured content now (via different paths)
        result_default = await tool_default.run({"x": 5})
        result_explicit_none = await tool_explicit_none.run({"x": 5})

        assert result_default.structured_content == {
            "result": "Result: 5"
        }  # Inherits wrapping
        assert result_explicit_none.structured_content == {
            "result": "Result: 5"
        }  # Generated via fallback logic
        assert isinstance(result_default.content[0], TextContent)
        assert isinstance(result_explicit_none.content[0], TextContent)
        assert result_default.content[0].text == result_explicit_none.content[0].text

    async def test_transform_output_schema_with_tool_result_return(
        self, base_string_tool
    ):
        """Test transform when custom function returns ToolResult directly."""

        async def custom_fn(x: int) -> ToolResult:
            # Custom function returns ToolResult - should bypass schema handling
            return ToolResult(
                content=[TextContent(type="text", text=f"Direct: {x}")],
                structured_content={"direct_value": x, "doubled": x * 2},
            )

        new_tool = Tool.from_tool(base_string_tool, transform_fn=custom_fn)

        # ToolResult return type should result in None output schema
        assert new_tool.output_schema is None

        result = await new_tool.run({"x": 6})
        # Should use ToolResult content directly
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "Direct: 6"
        assert result.structured_content == {"direct_value": 6, "doubled": 12}


class TestInputSchema:
    """Test schema definition handling and reference finding."""

    def test_arg_transform_examples_in_schema(self, add_tool: Tool):
        # Simple example
        new_tool = Tool.from_tool(
            add_tool,
            transform_args={
                "old_x": ArgTransform(examples=[1, 2, 3]),
            },
        )
        prop = get_property(new_tool, "old_x")
        assert prop["examples"] == [1, 2, 3]

        # Nested example (e.g., for array type)
        new_tool2 = Tool.from_tool(
            add_tool,
            transform_args={
                "old_x": ArgTransform(examples=[["a", "b"], ["c", "d"]]),
            },
        )
        prop2 = get_property(new_tool2, "old_x")
        assert prop2["examples"] == [["a", "b"], ["c", "d"]]

        # If not set, should not be present
        new_tool3 = Tool.from_tool(
            add_tool,
            transform_args={
                "old_x": ArgTransform(),
            },
        )
        prop3 = get_property(new_tool3, "old_x")
        assert "examples" not in prop3

    def test_merge_schema_with_defs_precedence(self):
        """Test _merge_schema_with_precedence merges $defs correctly.

        Note: This tests the raw merge behavior before dereferencing.
        The final schema output will be dereferenced by compress_schema.
        """
        base_schema = {
            "type": "object",
            "properties": {"field1": {"$ref": "#/$defs/BaseType"}},
            "$defs": {
                "BaseType": {"type": "string", "description": "base"},
                "SharedType": {"type": "integer", "minimum": 0},
            },
        }

        override_schema = {
            "type": "object",
            "properties": {"field2": {"$ref": "#/$defs/OverrideType"}},
            "$defs": {
                "OverrideType": {"type": "boolean"},
                "SharedType": {"type": "integer", "minimum": 10},  # Override
            },
        }

        transformed_tool_schema = TransformedTool._merge_schema_with_precedence(
            base_schema, override_schema
        )

        # SharedType should no longer be present on the schema (unused)
        assert "SharedType" not in transformed_tool_schema.get("$defs", {})

        # Schema is dereferenced so no $defs in final output
        assert transformed_tool_schema == snapshot(
            {
                "type": "object",
                "properties": {
                    "field1": {"type": "string", "description": "base"},
                    "field2": {"type": "boolean"},
                },
                "required": [],
            }
        )

    def test_transform_tool_with_complex_defs_pruning(self):
        """Test that tool transformation properly handles hidden params.

        With schema dereferencing, unused types are automatically removed
        since $defs is eliminated entirely.
        """

        class UsedType(BaseModel):
            value: str

        class UnusedType(BaseModel):
            other: int

        @Tool.from_function
        def complex_tool(
            used_param: UsedType, unused_param: UnusedType | None = None
        ) -> str:
            return used_param.value

        # Transform to hide unused_param
        transformed_tool: TransformedTool = Tool.from_tool(
            complex_tool, transform_args={"unused_param": ArgTransform(hide=True)}
        )

        # Schema is dereferenced - no $defs
        assert "$defs" not in transformed_tool.parameters

        assert transformed_tool.parameters == snapshot(
            {
                "type": "object",
                "properties": {
                    "used_param": {
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "type": "object",
                    }
                },
                "required": ["used_param"],
            }
        )

    def test_transform_with_custom_function_preserves_needed_types(self):
        """Test that custom transform functions preserve necessary types inline."""

        class InputType(BaseModel):
            data: str

        class OutputType(BaseModel):
            result: str

        @Tool.from_function
        def base_tool(input_data: InputType) -> OutputType:
            return OutputType(result=input_data.data.upper())

        async def transform_function(renamed_input: InputType):
            return await forward(renamed_input=renamed_input)

        # Transform with custom function and argument rename
        transformed = Tool.from_tool(
            base_tool,
            transform_fn=transform_function,
            transform_args={"input_data": ArgTransform(name="renamed_input")},
        )

        # Schema is dereferenced - types are inlined
        assert "$defs" not in transformed.parameters

        assert transformed.parameters == snapshot(
            {
                "type": "object",
                "properties": {
                    "renamed_input": {
                        "properties": {"data": {"type": "string"}},
                        "required": ["data"],
                        "type": "object",
                    }
                },
                "required": ["renamed_input"],
            }
        )

    def test_chained_transforms_inline_types(self):
        """Test that chained transformations produce correct inlined schemas."""

        class TypeA(BaseModel):
            a: str

        class TypeB(BaseModel):
            b: int

        class TypeC(BaseModel):
            c: bool

        @Tool.from_function
        def base_tool(param_a: TypeA, param_b: TypeB, param_c: TypeC) -> str:
            return f"{param_a.a}-{param_b.b}-{param_c.c}"

        # First transform: hide param_c
        transform1 = Tool.from_tool(
            base_tool,
            transform_args={"param_c": ArgTransform(hide=True, default=TypeC(c=True))},
        )

        # Schema is dereferenced - types are inlined
        assert "$defs" not in transform1.parameters

        assert transform1.parameters == snapshot(
            {
                "type": "object",
                "properties": {
                    "param_a": {
                        "properties": {"a": {"type": "string"}},
                        "required": ["a"],
                        "type": "object",
                    },
                    "param_b": {
                        "properties": {"b": {"type": "integer"}},
                        "required": ["b"],
                        "type": "object",
                    },
                },
                "required": IsList("param_b", "param_a", check_order=False),
            }
        )

        # Second transform: hide param_b
        transform2 = Tool.from_tool(
            transform1,
            transform_args={"param_b": ArgTransform(hide=True, default=TypeB(b=42))},
        )

        assert "$defs" not in transform2.parameters

        assert transform2.parameters == snapshot(
            {
                "type": "object",
                "properties": {
                    "param_a": {
                        "properties": {"a": {"type": "string"}},
                        "required": ["a"],
                        "type": "object",
                    }
                },
                "required": ["param_a"],
            }
        )
