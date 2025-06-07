import re
from typing import Annotated, Any

import pytest
from dirty_equals import IsList
from pydantic import Field
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
        add_tool, transform_args={"old_y": ArgTransform(drop=True)}
    )
    assert sorted(new_tool.parameters["properties"]) == ["old_x"]
    result = await new_tool.run(arguments={"old_x": 1})
    assert result[0].text == "11"  # type: ignore


async def test_dropped_args_error_if_provided(add_tool):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(drop=True)}
    )
    with pytest.raises(
        TypeError, match="Got unexpected keyword argument\\(s\\): old_y"
    ):
        await new_tool.run(arguments={"old_x": 1, "old_y": 2})


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
