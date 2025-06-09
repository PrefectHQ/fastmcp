import re
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

import pytest
from dirty_equals import IsList
from pydantic import BaseModel, Field
from rich import print  # type: ignore

from fastmcp import FastMCP
from fastmcp.client.client import Client
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


async def test_tool_change_arg_name_with_string(add_tool):
    new_tool = Tool.from_tool(add_tool, transform_args={"old_x": "new_x"})

    assert sorted(new_tool.parameters["properties"]) == ["new_x", "old_y"]
    assert get_property(new_tool, "new_x") == get_property(add_tool, "old_x")
    assert get_property(new_tool, "old_y") == get_property(add_tool, "old_y")
    assert new_tool.parameters["required"] == ["new_x"]
    result = await new_tool.run(arguments={"new_x": 1, "old_y": 2})
    assert result[0].text == "3"  # type: ignore


async def test_renamed_arg_description_is_maintained(add_tool):
    new_tool = Tool.from_tool(add_tool, transform_args={"old_x": "new_x"})
    assert get_property(new_tool, "new_x")["description"] == "old_x description"


async def test_tool_defaults_are_maintained_on_unmapped_args(add_tool):
    new_tool = Tool.from_tool(add_tool, transform_args={"old_x": "new_x"})
    result = await new_tool.run(arguments={"new_x": 1})
    assert result[0].text == "11"  # type: ignore


async def test_tool_defaults_are_maintained_on_mapped_args(add_tool):
    new_tool = Tool.from_tool(add_tool, transform_args={"old_y": "new_y"})
    result = await new_tool.run(arguments={"old_x": 1})
    assert result[0].text == "11"  # type: ignore


def test_tool_change_arg_name_with_arg_transform(add_tool):
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


async def test_tool_drop_arg_with_none(add_tool):
    # drop the arg with a default value
    new_tool = Tool.from_tool(add_tool, transform_args={"old_y": None})
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    result = await new_tool.run(arguments={"old_x": 1})
    assert result[0].text == "11"  # type: ignore


async def test_tool_drop_arg_with_arg_transform(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    result = await new_tool.run(arguments={"old_x": 1})
    assert result[0].text == "11"  # type: ignore


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
    assert result[0].text == "25"  # type: ignore


async def test_hidden_arg_without_default_uses_parent_default(add_tool):
    """Test that hidden argument without default uses parent's default."""
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    # Only old_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    # Should pass old_x=3 and let parent use its default old_y=10
    result = await new_tool.run(arguments={"old_x": 3})
    assert result[0].text == "13"  # type: ignore


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
            "old_x": "visible_x",  # Rename and expose
            "old_y": ArgTransform(hide=True, default=25),  # Hidden with constant
        },
    )

    # Only visible_x should be exposed
    assert sorted(new_tool.parameters["properties"]) == ["visible_x"]
    # Should pass visible_x=7 as old_x=7 and old_y=25 to parent
    result = await new_tool.run(arguments={"visible_x": 7})
    assert result[0].text == "32"  # type: ignore


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
    assert result[0].text == "25"  # type: ignore


async def test_forward_with_argument_mapping(add_tool):
    """Test that forward() applies argument mapping correctly."""

    async def custom_fn(new_x: int, new_y: int = 5) -> int:
        return await forward(new_x=new_x, new_y=new_y)

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": "new_x", "old_y": "new_y"},
    )

    result = await new_tool.run(arguments={"new_x": 2, "new_y": 3})
    assert result[0].text == "5"  # type: ignore


async def test_forward_with_incorrect_args_raises_error(add_tool):
    async def custom_fn(new_x: int, new_y: int = 5) -> int:
        # the forward should use the new args, not the old ones
        return await forward(old_x=new_x, old_y=new_y)

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": "new_x", "old_y": "new_y"},
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
        transform_args={"old_x": "new_x", "old_y": "new_y"},
    )

    result = await new_tool.run(arguments={"new_x": 2, "new_y": 3})
    assert result[0].text == "5"  # type: ignore


async def test_custom_fn_with_kwargs_and_no_transform_args(add_tool):
    async def custom_fn(extra: int, **kwargs) -> int:
        sum = await forward(**kwargs)
        return int(sum[0].text) + extra  # type: ignore[attr-defined]

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)
    result = await new_tool.run(arguments={"extra": 1, "old_x": 2, "old_y": 3})
    assert result[0].text == "6"  # type: ignore
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
    assert result[0].text == "5"  # type: ignore


async def test_fn_with_kwargs_receives_transformed_arg_names(add_tool):
    """Test that **kwargs receives arguments with their transformed names from transform_args."""

    async def custom_fn(new_x: int, **kwargs) -> int:
        # kwargs should contain 'old_y': 3 (transformed name), not 'old_y': 3 (original name)
        assert kwargs == {"old_y": 3}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool, transform_fn=custom_fn, transform_args={"old_x": "new_x"}
    )
    result = await new_tool.run(arguments={"new_x": 2, "old_y": 3})
    assert result[0].text == "5"  # type: ignore


async def test_fn_with_kwargs_handles_partial_explicit_args(add_tool):
    """Test that function can explicitly handle some transformed args while others pass through kwargs."""

    async def custom_fn(new_x: int, some_other_param: str = "default", **kwargs) -> int:
        # x is explicitly handled, y should come through kwargs with transformed name
        assert kwargs == {"old_y": 7}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool, transform_fn=custom_fn, transform_args={"old_x": "new_x"}
    )
    result = await new_tool.run(
        arguments={"new_x": 3, "old_y": 7, "some_other_param": "test"}
    )
    assert result[0].text == "10"  # type: ignore


async def test_fn_with_kwargs_mixed_mapped_and_unmapped_args(add_tool):
    """Test **kwargs behavior with mix of mapped and unmapped arguments."""

    async def custom_fn(new_x: int, **kwargs) -> int:
        # new_x is explicitly handled, old_y should pass through kwargs with original name (unmapped)
        assert kwargs == {"old_y": 5}
        result = await forward(new_x=new_x, **kwargs)
        return result

    new_tool = Tool.from_tool(
        add_tool, transform_fn=custom_fn, transform_args={"old_x": "new_x"}
    )  # only map 'a'
    result = await new_tool.run(arguments={"new_x": 1, "old_y": 5})
    assert result[0].text == "6"  # type: ignore


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
        transform_args={"old_x": "new_x", "old_y": None},
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
        Tool.from_tool(add_tool, transform_args={"unknown_param": "new_name"})


def test_transform_args_creates_duplicate_names(add_tool):
    """Test that transform_args creating duplicate parameter names raises ValueError."""
    with pytest.raises(
        ValueError,
        match="Multiple arguments would be mapped to the same names: same_name",
    ):
        Tool.from_tool(
            add_tool, transform_args={"old_x": "same_name", "old_y": "same_name"}
        )


def test_transform_args_creates_duplicate_names_with_arg_transform(add_tool):
    """Test that transform_args creating duplicate parameter names raises ValueError."""
    with pytest.raises(
        ValueError,
        match="Multiple arguments would be mapped to the same names: same_name",
    ):
        Tool.from_tool(
            add_tool,
            transform_args={
                "old_x": ArgTransform(name="same_name"),
                "old_y": "same_name",
            },
        )


def test_function_without_kwargs_missing_params(add_tool):
    """Test that function without **kwargs must declare all transformed params."""

    def invalid_fn(new_x: int, non_existent: str) -> str:
        return "test"

    with pytest.raises(
        ValueError,
        match="Function missing parameters required after transformation: new_y",
    ):
        Tool.from_tool(
            add_tool,
            transform_fn=invalid_fn,
            transform_args={"old_x": "new_x", "old_y": "new_y"},
        )


def test_function_without_kwargs_can_have_extra_params(add_tool):
    """Test that function without **kwargs can declare extra params beyond transformed ones."""

    def valid_fn(new_x: int, new_y: int, extra_param: str = "default") -> str:
        return f"{new_x + new_y}: {extra_param}"

    # This should work fine - function declares all required params plus an extra one
    tool = Tool.from_tool(
        add_tool,
        transform_fn=valid_fn,
        transform_args={"old_x": "new_x", "old_y": "new_y"},
    )

    # The final schema should include all function parameters
    assert "new_x" in tool.parameters["properties"]
    assert "new_y" in tool.parameters["properties"]
    assert "extra_param" in tool.parameters["properties"]


def test_function_with_kwargs_can_add_params(add_tool):
    """Test that function with **kwargs can add new parameters."""

    async def valid_fn(extra_param: str, **kwargs) -> str:
        result = await forward(**kwargs)
        return f"{extra_param}: {result}"

    # This should work fine - kwargs allows access to all transformed params
    tool = Tool.from_tool(
        add_tool,
        transform_fn=valid_fn,
        transform_args={"old_x": "new_x", "old_y": "new_y"},
    )

    # extra_param is added, new_x and new_y are available
    assert "extra_param" in tool.parameters["properties"]
    assert "new_x" in tool.parameters["properties"]
    assert "new_y" in tool.parameters["properties"]


async def test_chaining_transformations(add_tool):
    """Test that transformed tools can be transformed again."""
    # First transformation
    tool1 = Tool.from_tool(add_tool, transform_args={"old_x": "x"})

    # Second transformation on the already-transformed tool
    tool2 = Tool.from_tool(tool1, transform_args={"x": "final_x"})

    # Should work with the final names
    result = await tool2.run(arguments={"final_x": 5, "old_y": 3})
    assert result[0].text == "8"  # type: ignore

    # And forward() in a custom function should work
    async def custom(final_x: int, old_y: int) -> str:
        # forward() goes to tool1, which has 'final_x' and 'old_y' after transformation
        result = await forward(final_x=final_x, old_y=old_y)
        return f"Chained: {result}"

    tool3 = Tool.from_tool(tool1, transform_fn=custom, transform_args={"x": "final_x"})

    result = await tool3.run(arguments={"final_x": 5, "old_y": 3})
    assert "Chained:" in result[0].text  # type: ignore


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
    assert "42: test" in result[0].text  # type: ignore


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
    assert "String input '5'" in result[0].text  # type: ignore
    assert "result: 8" in result[0].text  # type: ignore


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
            add_tool, name="add_transformed", transform_args={"old_x": "new_x"}
        )
        proxy_server.add_tool(new_add_tool)

        async with Client(proxy_server) as client:
            # The tool should be registered with its transformed name
            result = await client.call_tool("add_transformed", {"new_x": 1, "old_y": 2})
            assert result[0].text == "3"  # type: ignore
