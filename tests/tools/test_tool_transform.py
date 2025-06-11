import re
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from dirty_equals import IsList
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, forward, forward_raw
from fastmcp.tools.tool import FunctionTool
from fastmcp.tools.tool_transform import ArgTransform, TransformedTool


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
    assert result[0].text == "11"  # type: ignore[attr-defined]


async def test_tool_defaults_are_maintained_on_mapped_args(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(name="new_y")}
    )
    result = await new_tool.run(arguments={"old_x": 1})
    assert result[0].text == "11"  # type: ignore[attr-defined]


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
    assert result[0].text == "11"  # type: ignore[attr-defined]


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
    assert result[0].text == "25"  # type: ignore[attr-defined]


async def test_hidden_arg_without_default_uses_parent_default(add_tool):
    """Test that hidden argument without default uses parent's default."""
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    # Only old_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    # Should pass old_x=3 and let parent use its default old_y=10
    result = await new_tool.run(arguments={"old_x": 3})
    assert result[0].text == "13"  # type: ignore[attr-defined]


async def test_mixed_hidden_args_with_custom_function(add_tool):
    """Test custom function with both hidden constant and hidden default parameters."""

    async def custom_fn(visible_x: int) -> int:
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
    assert result[0].text == "32"  # type: ignore[attr-defined]


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
    assert result[0].text == "25"  # type: ignore[attr-defined]


async def test_forward_with_argument_mapping(add_tool):
    """Test that forward() applies argument mapping correctly."""

    async def custom_fn(new_x: int, new_y: int = 5) -> int:
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
    assert result[0].text == "5"  # type: ignore[attr-defined]


async def test_forward_with_incorrect_args_raises_error(add_tool):
    async def custom_fn(new_x: int, new_y: int = 5) -> int:
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

    async def custom_fn(new_x: int, new_y: int = 5) -> int:
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
    assert result[0].text == "5"  # type: ignore[attr-defined]


async def test_custom_fn_with_kwargs_and_no_transform_args(add_tool):
    async def custom_fn(extra: int, **kwargs) -> int:
        sum = await forward(**kwargs)
        return int(sum[0].text) + extra  # type: ignore[attr-defined]

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)
    result = await new_tool.run(arguments={"extra": 1, "old_x": 2, "old_y": 3})
    assert result[0].text == "6"  # type: ignore[attr-defined]
    assert new_tool.parameters["required"] == IsList(
        "extra", "old_x", check_order=False
    )
    assert list(new_tool.parameters["properties"]) == IsList(
        "extra", "old_x", "old_y", check_order=False
    )


async def test_fn_with_kwargs_passes_through_original_args(add_tool):
    async def custom_fn(new_y: int = 5, **kwargs) -> int:
        assert kwargs == {"old_y": 3}
        result = await forward(old_x=new_y, **kwargs)
        return result

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)
    result = await new_tool.run(arguments={"new_y": 2, "old_y": 3})
    assert result[0].text == "5"  # type: ignore[attr-defined]


async def test_fn_with_kwargs_receives_transformed_arg_names(add_tool):
    """Test that **kwargs receives arguments with their transformed names from transform_args."""

    async def custom_fn(new_x: int, **kwargs) -> int:
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
    assert result[0].text == "5"  # type: ignore[attr-defined]


async def test_fn_with_kwargs_handles_partial_explicit_args(add_tool):
    """Test that function can explicitly handle some transformed args while others pass through kwargs."""

    async def custom_fn(new_x: int, some_other_param: str = "default", **kwargs) -> int:
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
    assert result[0].text == "10"  # type: ignore[attr-defined]


async def test_fn_with_kwargs_mixed_mapped_and_unmapped_args(add_tool):
    """Test **kwargs behavior with mix of mapped and unmapped arguments."""

    async def custom_fn(new_x: int, **kwargs) -> int:
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
    assert result[0].text == "6"  # type: ignore[attr-defined]


async def test_fn_with_kwargs_dropped_args_not_in_kwargs(add_tool):
    """Test that dropped arguments don't appear in **kwargs."""

    async def custom_fn(new_x: int, **kwargs) -> int:
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
    assert result[0].text == "18"  # type: ignore[attr-defined]


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


def test_transform_args_validation_unknown_arg(add_tool):
    """Test that transform_args with unknown arguments raises ValueError."""
    with pytest.raises(
        ValueError, match="Unknown arguments in transform_args: unknown_param"
    ):
        Tool.from_tool(
            add_tool, transform_args={"unknown_param": ArgTransform(name="new_name")}
        )


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
    assert result[0].text == "15"  # type: ignore[attr-defined]

    # Transform tool1 with custom function that handles all parameters
    async def custom(final_x: int, **kwargs) -> str:
        result = await forward(final_x=final_x, **kwargs)
        return f"custom {result[0].text}"  # Extract text from content

    tool3 = Tool.from_tool(
        tool1, transform_fn=custom, transform_args={"x": ArgTransform(name="final_x")}
    )
    result = await tool3.run(arguments={"final_x": 3, "old_y": 5})
    assert result[0].text == "custom 8"  # type: ignore[attr-defined]


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
        return f"custom: {result}"

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
    assert "42: test" in result[0].text  # type: ignore[attr-defined]


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
        result_text = result[0].text
        return f"String input '{x}' converted to result: {result_text}"

    tool = Tool.from_tool(
        base, transform_fn=custom_fn, transform_args={"x": ArgTransform(type=str)}
    )

    # Verify schema shows string type
    assert get_property(tool, "x")["type"] == "string"

    # Test it works with string input
    result = await tool.run(arguments={"x": "5", "y": 3})
    assert "String input '5'" in result[0].text  # type: ignore[attr-defined]
    assert "result: 8" in result[0].text  # type: ignore[attr-defined]


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

        proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(mcp_server)))
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
            assert result[0].text == "3"  # type: ignore[attr-defined]


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
    assert result[0].text == "42_12345.0"  # type: ignore[attr-defined]


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
    assert result1[0].text == "1_1"  # type: ignore[attr-defined]

    # Second call should get a different value
    result2 = await new_tool.run(arguments={"x": 2})
    assert result2[0].text == "2_2"  # type: ignore[attr-defined]


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
    assert result[0].text == "42_req_123"  # type: ignore[attr-defined]


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
    assert result[0].text == "value: 100"  # type: ignore

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
        Tool.from_tool(
            base_tool,
            transform_args={"required_param": ArgTransform(required=False, default=99)},  # type: ignore
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
    assert result[0].text == "value: 200"  # type: ignore


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
    assert result[0].text == "values: 1, 42"  # type: ignore


async def test_arg_transform_hide_and_required_raises_error():
    """Test that hide=True and required=True together raises an error."""
    with pytest.raises(
        ValueError, match="Cannot specify both 'hide=True' and 'required=True'"
    ):
        ArgTransform(hide=True, required=True)


class TestEnableDisable:
    async def test_transform_disabled_tool(self):
        """
        Tests that a transformed tool can run even if the parent tool is disabled
        """
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def add(x: int, y: int = 10) -> int:
            return x + y

        new_add = Tool.from_tool(add, name="new_add")
        mcp.add_tool(new_add)

        assert new_add.enabled

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"new_add"}

            result = await client.call_tool("new_add", {"x": 1, "y": 2})
            assert result[0].text == "3"  # type: ignore[attr-defined]

            with pytest.raises(ToolError):
                await client.call_tool("add", {"x": 1, "y": 2})

    async def test_disable_transformed_tool(self):
        mcp = FastMCP()

        @mcp.tool(enabled=False)
        def add(x: int, y: int = 10) -> int:
            return x + y

        new_add = Tool.from_tool(add, name="new_add", enabled=False)
        mcp.add_tool(new_add)

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError):
                await client.call_tool("new_add", {"x": 1, "y": 2})


def test_arg_transform_examples_in_schema(add_tool):
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
