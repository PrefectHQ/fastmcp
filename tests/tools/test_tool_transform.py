import re
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from dirty_equals import IsList
from inline_snapshot import snapshot
from mcp.types import TextContent
from pydantic import BaseModel, Field, TypeAdapter
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, forward, forward_raw
from fastmcp.tools.tool import FunctionTool, ToolResult
from fastmcp.tools.tool_transform import (
    ArgTransform,
    ToolTransformConfig,
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


def test_tool_from_tool_no_change(add_tool):
    new_tool = Tool.from_tool(add_tool)
    assert isinstance(new_tool, TransformedTool)
    assert new_tool.parameters == add_tool.parameters
    assert new_tool.name == add_tool.name
    assert new_tool.description == add_tool.description


async def test_renamed_arg_description_is_maintained(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(name="new_x")}
    )
    assert (
        new_tool.parameters["properties"]["new_x"]["description"] == "old_x description"
    )


async def test_tool_defaults_are_maintained_on_unmapped_args(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(name="new_x")}
    )
    result = await new_tool.run(arguments={"new_x": 1})
    # The parent tool returns int which gets wrapped as structured output
    assert result.structured_content == {"result": 11}


async def test_tool_defaults_are_maintained_on_mapped_args(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(name="new_y")}
    )
    result = await new_tool.run(arguments={"old_x": 1})
    # The parent tool returns int which gets wrapped as structured output
    assert result.structured_content == {"result": 11}


def test_tool_change_arg_name(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(name="new_x")}
    )

    assert sorted(new_tool.parameters["properties"]) == ["new_x", "old_y"]
    assert get_property(new_tool, "new_x") == get_property(add_tool, "old_x")
    assert get_property(new_tool, "old_y") == get_property(add_tool, "old_y")
    assert new_tool.parameters["required"] == ["new_x"]


def test_tool_change_arg_description(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(description="new description")}
    )
    assert get_property(new_tool, "old_x")["description"] == "new description"


async def test_tool_drop_arg(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    result = await new_tool.run(arguments={"old_x": 1})
    assert result.structured_content == {"result": 11}


async def test_dropped_args_error_if_provided(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    with pytest.raises(
        TypeError, match="Got unexpected keyword argument\\(s\\): old_y"
    ):
        await new_tool.run(arguments={"old_x": 1, "old_y": 2})


async def test_hidden_arg_with_constant_default(add_tool):
    """Test that hidden argument with default value passes constant to parent."""
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True, default=20)}
    )
    # Only old_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    # Should pass old_x=5 and old_y=20 to parent
    result = await new_tool.run(arguments={"old_x": 5})
    assert result.structured_content == {"result": 25}


async def test_hidden_arg_without_default_uses_parent_default(add_tool):
    """Test that hidden argument without default uses parent's default."""
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    # Only old_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    # Should pass old_x=3 and let parent use its default old_y=10
    result = await new_tool.run(arguments={"old_x": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "13"
    assert result.structured_content == {"result": 13}


async def test_mixed_hidden_args_with_custom_function(add_tool):
    """Test custom function with both hidden constant and hidden default parameters."""

    async def custom_fn(visible_x: int) -> ToolResult:
        # This custom function should receive the transformed visible parameter
        # and the hidden parameters should be automatically handled
        result = await forward(visible_x=visible_x)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="visible_x"),  # Rename and expose
            "old_y": ArgTransform(hide=True, default=25),  # Hidden with constant
        },
    )

    # Only visible_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["visible_x"]
    # Should pass visible_x=7 as old_x=7 and old_y=25 to parent
    result = await new_tool.run(arguments={"visible_x": 7})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "32"
    assert result.structured_content == {"result": 32}


async def test_hide_required_param_without_default_raises_error():
    """Test that hiding a required parameter without providing default raises error."""

    @Tool.from_function
    def tool_with_required_param(required_param: int, optional_param: int = 10) -> int:
        return required_param + optional_param

    # This should raise an error because required_param has no default and we're not providing one
    with pytest.raises(
        ValueError,
        match=r"Hidden parameter 'required_param' has no default value in parent tool",
    ):
        Tool.from_tool(
            tool_with_required_param,
            transform_args={"required_param": ArgTransform(hide=True)},
        )


async def test_hide_required_param_with_user_default_works():
    """Test that hiding a required parameter works when user provides a default."""

    @Tool.from_function
    def tool_with_required_param(required_param: int, optional_param: int = 10) -> int:
        return required_param + optional_param

    # This should work because we're providing a default for the hidden required param
    new_tool = Tool.from_tool(
        tool_with_required_param,
        transform_args={"required_param": ArgTransform(hide=True, default=5)},
    )

    # Only optional_param should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["optional_param"]
    # Should pass required_param=5 and optional_param=20 to parent
    result = await new_tool.run(arguments={"optional_param": 20})
    assert result.structured_content == {"result": 25}


async def test_hidden_param_prunes_defs():
    class VisibleType(BaseModel):
        x: int

    class HiddenType(BaseModel):
        y: int

    @Tool.from_function
    def tool_with_refs(a: VisibleType, b: HiddenType | None = None) -> int:
        return a.x + (b.y if b else 0)

    # Hide parameter 'b'
    new_tool = Tool.from_tool(
        tool_with_refs, transform_args={"b": ArgTransform(hide=True)}
    )

    schema = new_tool.parameters
    # Only 'a' should be visible
    assert list(schema["properties"].keys()) == ["a"]
    # $defs should only contain VisibleType, not HiddenType
    defs = schema.get("$defs", {})
    assert "VisibleType" in defs
    assert "HiddenType" not in defs


async def test_forward_with_argument_mapping(add_tool):
    """Test that forward() applies argument mapping correctly."""

    async def custom_fn(new_x: int, new_y: int = 5) -> ToolResult:
        return await forward(new_x=new_x, new_y=new_y)

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(name="new_y"),
        },
    )

    result = await new_tool.run(arguments={"new_x": 2, "new_y": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "5"
    assert result.structured_content == {"result": 5}


async def test_forward_with_incorrect_args_raises_error(add_tool):
    async def custom_fn(new_x: int, new_y: int = 5) -> ToolResult:
        # the forward should use the new args, not the old ones
        return await forward(old_x=new_x, old_y=new_y)

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(name="new_y"),
        },
    )
    with pytest.raises(
        TypeError, match=re.escape("Got unexpected keyword argument(s): old_x, old_y")
    ):
        await new_tool.run(arguments={"new_x": 2, "new_y": 3})


async def test_forward_raw_without_argument_mapping(add_tool):
    """Test that forward_raw() calls parent directly without mapping."""

    async def custom_fn(new_x: int, new_y: int = 5) -> ToolResult:
        # Call parent directly with original argument names
        result = await forward_raw(old_x=new_x, old_y=new_y)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(name="new_y"),
        },
    )

    result = await new_tool.run(arguments={"new_x": 2, "new_y": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "5"
    assert result.structured_content == {"result": 5}


async def test_custom_fn_with_kwargs_and_no_transform_args(add_tool):
    async def custom_fn(extra: int, **kwargs) -> int:
        sum = await forward(**kwargs)
        assert isinstance(sum.content[0], TextContent)
        return int(sum.content[0].text) + extra

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)
    result = await new_tool.run(arguments={"extra": 1, "old_x": 2, "old_y": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "6"
    assert result.structured_content == {"result": 6}
    assert new_tool.parameters["required"] == IsList(
        "extra", "old_x", check_order=False
    )
    assert list(new_tool.parameters["properties"]) == IsList(
        "extra", "old_x", "old_y", check_order=False
    )


async def test_fn_with_kwargs_passes_through_original_args(add_tool):
    async def custom_fn(new_y: int = 5, **kwargs) -> ToolResult:
        assert kwargs == {"old_y": 3}
        result = await forward(old_x=new_y, **kwargs)
        return result

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)
    result = await new_tool.run(arguments={"new_y": 2, "old_y": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "5"
    assert result.structured_content == {"result": 5}


async def test_fn_with_kwargs_receives_transformed_arg_names(add_tool):
    """Test that **kwargs receives arguments with their transformed names from transform_args."""

    async def custom_fn(new_x: int, **kwargs) -> ToolResult:
        # kwargs should contain 'old_y': 3 (transformed name), not 'old_y': 3 (original name)
        assert kwargs == {"old_y": 3}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )
    result = await new_tool.run(arguments={"new_x": 2, "old_y": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "5"
    assert result.structured_content == {"result": 5}


async def test_fn_with_kwargs_handles_partial_explicit_args(add_tool):
    """Test that function can explicitly handle some transformed args while others pass through kwargs."""

    async def custom_fn(
        new_x: int, some_other_param: str = "default", **kwargs
    ) -> ToolResult:
        # x is explicitly handled, y should come through kwargs with transformed name
        assert kwargs == {"old_y": 7}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )
    result = await new_tool.run(
        arguments={"new_x": 3, "old_y": 7, "some_other_param": "test"}
    )
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "10"
    assert result.structured_content == {"result": 10}


async def test_fn_with_kwargs_mixed_mapped_and_unmapped_args(add_tool):
    """Test **kwargs behavior with mix of mapped and unmapped arguments."""

    async def custom_fn(new_x: int, **kwargs) -> ToolResult:
        # new_x is explicitly handled, old_y should pass through kwargs with original name (unmapped)
        assert kwargs == {"old_y": 5}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )  # only map 'a'
    result = await new_tool.run(arguments={"new_x": 1, "old_y": 5})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "6"
    assert result.structured_content == {"result": 6}


async def test_fn_with_kwargs_dropped_args_not_in_kwargs(add_tool):
    """Test that dropped arguments don't appear in **kwargs."""

    async def custom_fn(new_x: int, **kwargs) -> ToolResult:
        # 'b' was dropped, so kwargs should be empty
        assert kwargs == {}
        # Can't use 'old_y' since it was dropped, so just use 'old_x' mapped to 'new_x'
        result = await forward(new_x=new_x)
        return result

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(hide=True),
        },
    )  # drop 'old_y'
    result = await new_tool.run(arguments={"new_x": 8})
    # 8 + 10 (default value of b in parent)
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "18"


async def test_forward_outside_context_raises_error():
    """Test that forward() raises RuntimeError when called outside a transformed tool."""
    with pytest.raises(
        RuntimeError,
        match=re.escape("forward() can only be called within a transformed tool"),
    ):
        await forward(new_x=1, old_y=2)


async def test_forward_raw_outside_context_raises_error():
    """Test that forward_raw() raises RuntimeError when called outside a transformed tool."""
    with pytest.raises(
        RuntimeError,
        match=re.escape("forward_raw() can only be called within a transformed tool"),
    ):
        await forward_raw(new_x=1, old_y=2)


def test_transform_args_with_parent_defaults():
    """Test that transform_args with parent defaults works."""

    class CoolModel(BaseModel):
        x: int = 10

    def parent_tool(cool_model: CoolModel) -> int:
        return cool_model.x

    tool = Tool.from_function(parent_tool)

    new_tool = Tool.from_tool(tool)

    assert new_tool.parameters["$defs"] == tool.parameters["$defs"]


def test_transform_args_validation_unknown_arg(add_tool):
    """Test that transform_args with unknown arguments raises ValueError."""
    with pytest.raises(
        ValueError, match="Unknown arguments in transform_args: unknown_param"
    ) as exc_info:
        Tool.from_tool(
            add_tool, transform_args={"unknown_param": ArgTransform(name="new_name")}
        )

    assert "`add`" in str(exc_info.value)


def test_transform_args_creates_duplicate_names(add_tool):
    """Test that transform_args creating duplicate parameter names raises ValueError."""
    with pytest.raises(
        ValueError,
        match="Multiple arguments would be mapped to the same names: same_name",
    ):
        Tool.from_tool(
            add_tool,
            transform_args={
                "old_x": ArgTransform(name="same_name"),
                "old_y": ArgTransform(name="same_name"),
            },
        )


def test_function_without_kwargs_missing_params(add_tool):
    """Test that function missing required transformed parameters raises ValueError."""

    def invalid_fn(new_x: int, non_existent: str) -> str:
        return f"{new_x}_{non_existent}"

    with pytest.raises(
        ValueError,
        match="Function missing parameters required after transformation: new_y",
    ):
        Tool.from_tool(
            add_tool,
            transform_fn=invalid_fn,
            transform_args={
                "old_x": ArgTransform(name="new_x"),
                "old_y": ArgTransform(name="new_y"),
            },
        )


def test_function_without_kwargs_can_have_extra_params(add_tool):
    """Test that function can have extra parameters not in parent tool."""

    def valid_fn(new_x: int, new_y: int, extra_param: str = "default") -> str:
        return f"{new_x}_{new_y}_{extra_param}"

    # Should work - extra_param is fine as long as it has a default
    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=valid_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(name="new_y"),
        },
    )

    # The final schema should include all function parameters
    assert "new_x" in new_tool.parameters["properties"]
    assert "new_y" in new_tool.parameters["properties"]
    assert "extra_param" in new_tool.parameters["properties"]


def test_function_with_kwargs_can_add_params(add_tool):
    """Test that function with **kwargs can add new parameters."""

    async def valid_fn(extra_param: str, **kwargs) -> str:
        result = await forward(**kwargs)
        return f"{extra_param}: {result}"

    # This should work fine - kwargs allows access to all transformed params
    tool = Tool.from_tool(
        add_tool,
        transform_fn=valid_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(name="new_y"),
        },
    )

    # extra_param is added, new_x and new_y are available
    assert "extra_param" in tool.parameters["properties"]
    assert "new_x" in tool.parameters["properties"]
    assert "new_y" in tool.parameters["properties"]


async def test_tool_transform_chaining(add_tool):
    """Test that transformed tools can be transformed again."""
    # First transformation: a -> x
    tool1 = Tool.from_tool(add_tool, transform_args={"old_x": ArgTransform(name="x")})

    # Second transformation: x -> final_x, using tool1
    tool2 = Tool.from_tool(tool1, transform_args={"x": ArgTransform(name="final_x")})

    result = await tool2.run(arguments={"final_x": 5})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "15"

    # Transform tool1 with custom function that handles all parameters
    async def custom(final_x: int, **kwargs) -> str:
        result = await forward(final_x=final_x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"custom {result.content[0].text}"  # Extract text from content

    tool3 = Tool.from_tool(
        tool1, transform_fn=custom, transform_args={"x": ArgTransform(name="final_x")}
    )
    result = await tool3.run(arguments={"final_x": 3, "old_y": 5})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "custom 8"


class MyModel(BaseModel):
    x: int
    y: str


@dataclass
class MyDataclass:
    x: int
    y: str


class MyTypedDict(TypedDict):
    x: int
    y: str


@pytest.mark.parametrize(
    "py_type, json_type",
    [
        (int, "integer"),
        (float, "number"),
        (str, "string"),
        (bool, "boolean"),
        (list, "array"),
        (list[int], "array"),
        (dict, "object"),
        (dict[str, int], "object"),
        (MyModel, "object"),
        (MyDataclass, "object"),
        (MyTypedDict, "object"),
    ],
)
def test_arg_transform_type_handling(add_tool, py_type, json_type):
    """Test that ArgTransform type attribute gets applied to schema."""
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(type=py_type)}
    )

    # Check that the type was changed in the schema
    x_prop = get_property(new_tool, "old_x")
    assert x_prop["type"] == json_type


def test_arg_transform_annotated_types(add_tool):
    """Test that ArgTransform works with annotated types and complex types."""
    from typing import Annotated

    from pydantic import Field

    # Test with Annotated types
    tool = Tool.from_tool(
        add_tool,
        transform_args={
            "old_x": ArgTransform(
                type=Annotated[int, Field(description="An annotated integer")]
            )
        },
    )

    x_prop = get_property(tool, "old_x")
    assert x_prop["type"] == "integer"
    # The ArgTransform description should override the annotation description
    # (since we didn't set a description in ArgTransform, it should use the original)

    # Test with Annotated string that has constraints
    tool2 = Tool.from_tool(
        add_tool,
        transform_args={
            "old_x": ArgTransform(
                type=Annotated[str, Field(min_length=1, max_length=10)]
            )
        },
    )

    x_prop2 = get_property(tool2, "old_x")
    assert x_prop2["type"] == "string"
    assert x_prop2["minLength"] == 1
    assert x_prop2["maxLength"] == 10


def test_arg_transform_precedence_over_function_without_kwargs():
    """Test that ArgTransform attributes take precedence over function signature (no **kwargs)."""

    @Tool.from_function
    def base(x: int, y: str = "default") -> str:
        return f"{x}: {y}"

    # Function signature says x: int with no default, y: str = "function_default"
    # ArgTransform should override these
    def custom_fn(x: str = "transform_default", y: int = 99) -> str:
        return f"custom: {x}, {y}"

    tool = Tool.from_tool(
        base,
        transform_fn=custom_fn,
        transform_args={
            "x": ArgTransform(type=str, default="transform_default"),
            "y": ArgTransform(type=int, default=99),
        },
    )

    # ArgTransform should take precedence
    x_prop = get_property(tool, "x")
    y_prop = get_property(tool, "y")

    assert x_prop["type"] == "string"  # ArgTransform type wins
    assert x_prop["default"] == "transform_default"  # ArgTransform default wins
    assert y_prop["type"] == "integer"  # ArgTransform type wins
    assert y_prop["default"] == 99  # ArgTransform default wins

    # Neither parameter should be required due to ArgTransform defaults
    assert "x" not in tool.parameters["required"]
    assert "y" not in tool.parameters["required"]


async def test_arg_transform_precedence_over_function_with_kwargs():
    """Test that ArgTransform attributes take precedence over function signature (with **kwargs)."""

    @Tool.from_function
    def base(x: int, y: str = "base_default") -> str:
        return f"{x}: {y}"

    # Function signature has different types/defaults than ArgTransform
    async def custom_fn(x: str = "function_default", **kwargs) -> str:
        result = await forward(x=x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"custom: {result.content[0].text}"

    tool = Tool.from_tool(
        base,
        transform_fn=custom_fn,
        transform_args={
            "x": ArgTransform(type=int, default=42),  # Different type and default
            "y": ArgTransform(description="ArgTransform description"),
        },
    )

    # ArgTransform should take precedence
    x_prop = get_property(tool, "x")
    y_prop = get_property(tool, "y")

    assert x_prop["type"] == "integer"  # ArgTransform type wins over function's str
    assert x_prop["default"] == 42  # ArgTransform default wins over function's default
    assert (
        y_prop["description"] == "ArgTransform description"
    )  # ArgTransform description

    # x should not be required due to ArgTransform default
    assert "x" not in tool.parameters["required"]

    # Test it works at runtime
    result = await tool.run(arguments={"y": "test"})
    # Should use ArgTransform default of 42
    assert isinstance(result.content[0], TextContent)
    assert "42: test" in result.content[0].text


def test_arg_transform_combined_attributes():
    """Test that multiple ArgTransform attributes work together."""

    @Tool.from_function
    def base(param: int) -> str:
        return str(param)

    tool = Tool.from_tool(
        base,
        transform_args={
            "param": ArgTransform(
                name="renamed_param",
                type=str,
                description="New description",
                default="default_value",
            )
        },
    )

    # Check all attributes were applied
    assert "renamed_param" in tool.parameters["properties"]
    assert "param" not in tool.parameters["properties"]

    prop = get_property(tool, "renamed_param")
    assert prop["type"] == "string"
    assert prop["description"] == "New description"
    assert prop["default"] == "default_value"
    assert "renamed_param" not in tool.parameters["required"]  # Has default


async def test_arg_transform_type_precedence_runtime():
    """Test that ArgTransform type changes work correctly at runtime."""

    @Tool.from_function
    def base(x: int, y: int = 10) -> int:
        return x + y

    # Transform x to string type but keep same logic
    async def custom_fn(x: str, y: int = 10) -> str:
        # Convert string back to int for the original function
        result = await forward_raw(x=int(x), y=y)
        # Extract the text from the result
        assert isinstance(result.content[0], TextContent)
        result_text = result.content[0].text
        return f"String input '{x}' converted to result: {result_text}"

    tool = Tool.from_tool(
        base, transform_fn=custom_fn, transform_args={"x": ArgTransform(type=str)}
    )

    # Verify schema shows string type
    assert get_property(tool, "x")["type"] == "string"

    # Test it works with string input
    result = await tool.run(arguments={"x": "5", "y": 3})
    assert isinstance(result.content[0], TextContent)
    assert "String input '5'" in result.content[0].text
    assert "result: 8" in result.content[0].text


class TestProxy:
    @pytest.fixture
    def mcp_server(self) -> FastMCP:
        mcp = FastMCP()

        @mcp.tool
        def add(old_x: int, old_y: int = 10) -> int:
            return old_x + old_y

        return mcp

    @pytest.fixture
    def proxy_server(self, mcp_server: FastMCP) -> FastMCP:
        from fastmcp.client.transports import FastMCPTransport

        proxy = FastMCP.as_proxy(FastMCPTransport(mcp_server))
        return proxy

    async def test_transform_proxy(self, proxy_server: FastMCP):
        # when adding transformed tools to proxy servers. Needs separate investigation.

        add_tool = await proxy_server.get_tool("add")
        new_add_tool = Tool.from_tool(
            add_tool,
            name="add_transformed",
            transform_args={"old_x": ArgTransform(name="new_x")},
        )
        proxy_server.add_tool(new_add_tool)

        async with Client(proxy_server) as client:
            # The tool should be registered with its transformed name
            result = await client.call_tool("add_transformed", {"new_x": 1, "old_y": 2})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "3"


async def test_arg_transform_default_factory():
    """Test ArgTransform with default_factory for hidden parameters."""

    @Tool.from_function
    def base_tool(x: int, timestamp: float) -> str:
        return f"{x}_{timestamp}"

    # Create a tool with default_factory for hidden timestamp
    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "timestamp": ArgTransform(hide=True, default_factory=lambda: 12345.0)
        },
    )

    # Only x should be visible since timestamp is hidden
    assert sorted(new_tool.parameters["properties"]) == ["x"]

    # Should work without providing timestamp (gets value from factory)
    result = await new_tool.run(arguments={"x": 42})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "42_12345.0"


async def test_arg_transform_default_factory_called_each_time():
    """Test that default_factory is called for each execution."""
    call_count = 0

    def counter_factory():
        nonlocal call_count
        call_count += 1
        return call_count

    @Tool.from_function
    def base_tool(x: int, counter: int = 0) -> str:
        return f"{x}_{counter}"

    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "counter": ArgTransform(hide=True, default_factory=counter_factory)
        },
    )

    # Only x should be visible since counter is hidden
    assert sorted(new_tool.parameters["properties"]) == ["x"]

    # First call
    result1 = await new_tool.run(arguments={"x": 1})
    assert isinstance(result1.content[0], TextContent)
    assert result1.content[0].text == "1_1"

    # Second call should get a different value
    result2 = await new_tool.run(arguments={"x": 2})
    assert isinstance(result2.content[0], TextContent)
    assert result2.content[0].text == "2_2"


async def test_arg_transform_hidden_with_default_factory():
    """Test hidden parameter with default_factory."""

    @Tool.from_function
    def base_tool(x: int, request_id: str) -> str:
        return f"{x}_{request_id}"

    def make_request_id():
        return "req_123"

    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "request_id": ArgTransform(hide=True, default_factory=make_request_id)
        },
    )

    # Only x should be visible
    assert sorted(new_tool.parameters["properties"]) == ["x"]

    # Should pass hidden request_id with factory value
    result = await new_tool.run(arguments={"x": 42})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "42_req_123"


async def test_arg_transform_default_and_factory_raises_error():
    """Test that providing both default and default_factory raises an error."""
    with pytest.raises(
        ValueError, match="Cannot specify both 'default' and 'default_factory'"
    ):
        ArgTransform(default=42, default_factory=lambda: 24)


async def test_arg_transform_default_factory_requires_hide():
    """Test that default_factory requires hide=True."""
    with pytest.raises(
        ValueError, match="default_factory can only be used with hide=True"
    ):
        ArgTransform(default_factory=lambda: 42)  # hide=False by default


async def test_arg_transform_required_true():
    """Test that required=True makes an optional parameter required."""

    @Tool.from_function
    def base_tool(optional_param: int = 42) -> str:
        return f"value: {optional_param}"

    # Make the optional parameter required
    new_tool = Tool.from_tool(
        base_tool, transform_args={"optional_param": ArgTransform(required=True)}
    )

    # Parameter should now be required (no default in schema)
    assert "optional_param" in new_tool.parameters["required"]
    assert "default" not in new_tool.parameters["properties"]["optional_param"]

    # Should work when parameter is provided
    result = await new_tool.run(arguments={"optional_param": 100})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "value: 100"

    # Should fail when parameter is not provided
    with pytest.raises(TypeError, match="Missing required argument"):
        await new_tool.run(arguments={})


async def test_arg_transform_required_false():
    """Test that required=False makes a required parameter optional with default."""

    @Tool.from_function
    def base_tool(required_param: int) -> str:
        return f"value: {required_param}"

    with pytest.raises(
        ValueError,
        match="Cannot specify 'required=False'. Set a default value instead.",
    ):
        # Intentionally passing invalid argument to test error handling
        Tool.from_tool(
            base_tool,
            transform_args={"required_param": ArgTransform(required=False, default=99)},  # type: ignore[arg-type]
        )


async def test_arg_transform_required_with_rename():
    """Test that required works correctly with argument renaming."""

    @Tool.from_function
    def base_tool(optional_param: int = 42) -> str:
        return f"value: {optional_param}"

    # Rename and make required
    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "optional_param": ArgTransform(name="new_param", required=True)
        },
    )

    # New parameter name should be required
    assert "new_param" in new_tool.parameters["required"]
    assert "optional_param" not in new_tool.parameters["properties"]
    assert "new_param" in new_tool.parameters["properties"]
    assert "default" not in new_tool.parameters["properties"]["new_param"]

    # Should work with new name
    result = await new_tool.run(arguments={"new_param": 200})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "value: 200"


async def test_arg_transform_required_true_with_default_raises_error():
    """Test that required=True with default raises an error."""
    with pytest.raises(
        ValueError, match="Cannot specify 'required=True' with 'default'"
    ):
        ArgTransform(required=True, default=42)


async def test_arg_transform_required_true_with_factory_raises_error():
    """Test that required=True with default_factory raises an error."""
    with pytest.raises(
        ValueError, match="default_factory can only be used with hide=True"
    ):
        ArgTransform(required=True, default_factory=lambda: 42)


async def test_arg_transform_required_no_change():
    """Test that required=... (NotSet) leaves requirement status unchanged."""

    @Tool.from_function
    def base_tool(required_param: int, optional_param: int = 42) -> str:
        return f"values: {required_param}, {optional_param}"

    # Transform without changing required status
    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "required_param": ArgTransform(name="req"),
            "optional_param": ArgTransform(name="opt"),
        },
    )

    # Required status should be unchanged
    assert "req" in new_tool.parameters["required"]
    assert "opt" not in new_tool.parameters["required"]
    assert new_tool.parameters["properties"]["opt"]["default"] == 42

    # Should work as expected
    result = await new_tool.run(arguments={"req": 1})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "values: 1, 42"


async def test_arg_transform_hide_and_required_raises_error():
    """Test that hide=True and required=True together raises an error."""
    with pytest.raises(
        ValueError, match="Cannot specify both 'hide=True' and 'required=True'"
    ):
        ArgTransform(hide=True, required=True)


class TestEnableDisable:
    async def test_transform_disabled_tool(self):
        """
        Tests that a transformed tool can run even if the parent tool is disabled via server.
        """
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int = 10) -> int:
            return x + y

        new_add = Tool.from_tool(add, name="new_add")
        mcp.add_tool(new_add)

        # Disable original tool, but new_add should still work
        mcp.disable(keys=["tool:add"])

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"new_add"}

            result = await client.call_tool("new_add", {"x": 1, "y": 2})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "3"

            with pytest.raises(ToolError):
                await client.call_tool("add", {"x": 1, "y": 2})

    async def test_disable_transformed_tool(self):
        mcp = FastMCP()

        @mcp.tool
        def add(x: int, y: int = 10) -> int:
            return x + y

        new_add = Tool.from_tool(add, name="new_add")
        mcp.add_tool(new_add)

        # Disable both tools via server
        mcp.disable(keys=["tool:add", "tool:new_add"])

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError):
                await client.call_tool("new_add", {"x": 1, "y": 2})


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


@pytest.fixture
def sample_tool():
    """Sample tool for testing transformations."""

    def sample_func(x: int) -> str:
        return f"Result: {x}"

    return Tool.from_function(
        sample_func,
        name="sample_tool",
        title="Original Tool Title",
        description="Original description",
    )


@pytest.fixture
def sample_tool_no_title():
    """Sample tool without title for testing."""

    def sample_func(x: int) -> str:
        return f"Result: {x}"

    return Tool.from_function(sample_func, name="no_title_tool")


def test_transform_inherits_title(sample_tool):
    """Test that transformed tools inherit title when none specified."""
    transformed = Tool.from_tool(sample_tool)
    assert transformed.title == "Original Tool Title"


def test_transform_overrides_title(sample_tool):
    """Test that transformed tools can override title."""
    transformed = Tool.from_tool(sample_tool, title="New Tool Title")
    assert transformed.title == "New Tool Title"


def test_transform_sets_title_to_none(sample_tool):
    """Test that transformed tools can explicitly set title to None."""
    transformed = Tool.from_tool(sample_tool, title=None)
    assert transformed.title is None


def test_transform_inherits_none_title(sample_tool_no_title):
    """Test that transformed tools inherit None title."""
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.title is None


def test_transform_adds_title_to_none(sample_tool_no_title):
    """Test that transformed tools can add title when parent has None."""
    transformed = Tool.from_tool(sample_tool_no_title, title="Added Title")
    assert transformed.title == "Added Title"


def test_transform_inherits_description(sample_tool):
    """Test that transformed tools inherit description when none specified."""
    transformed = Tool.from_tool(sample_tool)
    assert transformed.description == "Original description"


def test_transform_overrides_description(sample_tool):
    """Test that transformed tools can override description."""
    transformed = Tool.from_tool(sample_tool, description="New description")
    assert transformed.description == "New description"


def test_transform_sets_description_to_none(sample_tool):
    """Test that transformed tools can explicitly set description to None."""
    transformed = Tool.from_tool(sample_tool, description=None)
    assert transformed.description is None


def test_transform_inherits_none_description(sample_tool_no_title):
    """Test that transformed tools inherit None description."""
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.description is None


def test_transform_adds_description_to_none(sample_tool_no_title):
    """Test that transformed tools can add description when parent has None."""
    transformed = Tool.from_tool(sample_tool_no_title, description="Added description")
    assert transformed.description == "Added description"


# Meta transformation tests
def test_transform_inherits_meta(sample_tool):
    """Test that transformed tools inherit meta when none specified."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool)
    assert transformed.meta == {"original": True, "version": "1.0"}


def test_transform_overrides_meta(sample_tool):
    """Test that transformed tools can override meta."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool, meta={"custom": True, "priority": "high"})
    assert transformed.meta == {"custom": True, "priority": "high"}


def test_transform_sets_meta_to_none(sample_tool):
    """Test that transformed tools can explicitly set meta to None."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    transformed = Tool.from_tool(sample_tool, meta=None)
    assert transformed.meta is None


def test_transform_inherits_none_meta(sample_tool_no_title):
    """Test that transformed tools inherit None meta."""
    sample_tool_no_title.meta = None
    transformed = Tool.from_tool(sample_tool_no_title)
    assert transformed.meta is None


def test_transform_adds_meta_to_none(sample_tool_no_title):
    """Test that transformed tools can add meta when parent has None."""
    sample_tool_no_title.meta = None
    transformed = Tool.from_tool(sample_tool_no_title, meta={"added": True})
    assert transformed.meta == {"added": True}


def test_tool_transform_config_inherits_meta(sample_tool):
    """Test that ToolTransformConfig inherits meta when unset."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(name="config_tool")
    transformed = config.apply(sample_tool)
    assert transformed.meta == {"original": True, "version": "1.0"}


def test_tool_transform_config_overrides_meta(sample_tool):
    """Test that ToolTransformConfig can override meta."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(
        name="config_tool", meta={"config": True, "priority": "high"}
    )
    transformed = config.apply(sample_tool)
    assert transformed.meta == {"config": True, "priority": "high"}


def test_tool_transform_config_removes_meta(sample_tool):
    """Test that ToolTransformConfig can remove meta with None."""
    sample_tool.meta = {"original": True, "version": "1.0"}
    config = ToolTransformConfig(name="config_tool", meta=None)
    transformed = config.apply(sample_tool)
    assert transformed.meta is None


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
        """Test _merge_schema_with_precedence merges $defs correctly."""
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

        # SharedType should no longer be present on the schema
        assert "SharedType" not in transformed_tool_schema["$defs"]

        assert transformed_tool_schema == snapshot(
            {
                "type": "object",
                "properties": {
                    "field1": {"$ref": "#/$defs/BaseType"},
                    "field2": {"$ref": "#/$defs/OverrideType"},
                },
                "required": [],
                "$defs": {
                    "BaseType": {"type": "string", "description": "base"},
                    "OverrideType": {"type": "boolean"},
                },
            }
        )

    def test_transform_tool_with_complex_defs_pruning(self):
        """Test that tool transformation properly prunes unused $defs."""

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

        assert "UnusedType" not in transformed_tool.parameters["$defs"]

        assert transformed_tool.parameters == snapshot(
            {
                "type": "object",
                "properties": {"used_param": {"$ref": "#/$defs/UsedType"}},
                "required": ["used_param"],
                "$defs": {
                    "UsedType": {
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "type": "object",
                    }
                },
            }
        )

    def test_transform_with_custom_function_preserves_needed_defs(self):
        """Test that custom transform functions preserve necessary $defs."""

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

        assert transformed.parameters == snapshot(
            {
                "type": "object",
                "properties": {"renamed_input": {"$ref": "#/$defs/InputType"}},
                "required": ["renamed_input"],
                "$defs": {
                    "InputType": {
                        "properties": {"data": {"type": "string"}},
                        "required": ["data"],
                        "type": "object",
                    }
                },
            }
        )

    def test_chained_transforms_preserve_correct_defs(self):
        """Test that chained transformations preserve correct $defs."""

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

        assert transform1.parameters == snapshot(
            {
                "type": "object",
                "properties": {
                    "param_a": {"$ref": "#/$defs/TypeA"},
                    "param_b": {"$ref": "#/$defs/TypeB"},
                },
                "required": IsList("param_b", "param_a", check_order=False),
                "$defs": {
                    "TypeA": {
                        "properties": {"a": {"type": "string"}},
                        "required": ["a"],
                        "type": "object",
                    },
                    "TypeB": {
                        "properties": {"b": {"type": "integer"}},
                        "required": ["b"],
                        "type": "object",
                    },
                },
            }
        )

        assert "TypeA" in transform1.parameters["$defs"]

        # Second transform: hide param_b
        transform2 = Tool.from_tool(
            transform1,
            transform_args={"param_b": ArgTransform(hide=True, default=TypeB(b=42))},
        )

        assert "TypeB" not in transform2.parameters["$defs"]

        assert transform2.parameters == snapshot(
            {
                "type": "object",
                "properties": {"param_a": {"$ref": "#/$defs/TypeA"}},
                "required": ["param_a"],
                "$defs": {
                    "TypeA": {
                        "properties": {"a": {"type": "string"}},
                        "required": ["a"],
                        "type": "object",
                    }
                },
            }
        )
