"""Core tool transform functionality."""

import re
from typing import Annotated, Any

import pytest
from mcp.types import TextContent
from pydantic import BaseModel, Field

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.tools import Tool, forward, forward_raw
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
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_y": ArgTransform(hide=True)}
    )
    result = await new_tool.run(arguments={"old_x": 1})
    # old_y should use its default value of 10
    assert result.structured_content == {"result": 11}


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
    async def custom_fn(new_x: int, **kwargs) -> str:
        result = await forward(new_x=new_x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"Custom: {result.content[0].text}"

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(hide=True),
        },
    )

    result = await new_tool.run(arguments={"new_x": 5})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Custom: 15"


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
    # Schema should be fully dereferenced (no $defs)
    assert "$defs" not in schema
    # VisibleType should be inlined in the property
    assert schema["properties"]["a"] == {
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "type": "object",
    }


async def test_forward_with_argument_mapping(add_tool):
    async def custom_fn(new_x: int, **kwargs) -> str:
        result = await forward(new_x=new_x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"Mapped: {result.content[0].text}"

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )

    result = await new_tool.run(arguments={"new_x": 3, "old_y": 7})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Mapped: 10"


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
    async def custom_fn(**kwargs) -> str:
        # forward_raw passes through kwargs as-is
        result = await forward_raw(**kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"Raw: {result.content[0].text}"

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)

    result = await new_tool.run(arguments={"old_x": 2, "old_y": 8})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Raw: 10"


async def test_custom_fn_with_kwargs_and_no_transform_args(add_tool):
    async def custom_fn(**kwargs) -> str:
        result = await forward(**kwargs)
        assert isinstance(result.content[0], TextContent)
        return f"Custom: {result.content[0].text}"

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)

    result = await new_tool.run(arguments={"old_x": 4, "old_y": 6})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Custom: 10"


async def test_fn_with_kwargs_passes_through_original_args(add_tool):
    async def custom_fn(**kwargs) -> str:
        # Should receive original arg names
        assert "old_x" in kwargs
        assert "old_y" in kwargs
        result = await forward(**kwargs)
        assert isinstance(result.content[0], TextContent)
        return result.content[0].text

    new_tool = Tool.from_tool(add_tool, transform_fn=custom_fn)

    result = await new_tool.run(arguments={"old_x": 1, "old_y": 2})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "3"


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
    async def custom_fn(new_x: int, **kwargs) -> str:
        result = await forward(new_x=new_x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return result.content[0].text

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )

    # Only provide new_x, old_y should use default
    result = await new_tool.run(arguments={"new_x": 7})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "17"  # 7 + 10 (default)


async def test_fn_with_kwargs_mixed_mapped_and_unmapped_args(add_tool):
    async def custom_fn(new_x: int, old_y: int, **kwargs) -> str:
        result = await forward(new_x=new_x, old_y=old_y, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return result.content[0].text

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={"old_x": ArgTransform(name="new_x")},
    )

    result = await new_tool.run(arguments={"new_x": 2, "old_y": 8})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "10"


async def test_fn_with_kwargs_dropped_args_not_in_kwargs(add_tool):
    async def custom_fn(new_x: int, **kwargs) -> str:
        # old_y is dropped, so it shouldn't be in kwargs
        assert "old_y" not in kwargs
        result = await forward(new_x=new_x, **kwargs)
        assert isinstance(result.content[0], TextContent)
        return result.content[0].text

    new_tool = Tool.from_tool(
        add_tool,
        transform_fn=custom_fn,
        transform_args={
            "old_x": ArgTransform(name="new_x"),
            "old_y": ArgTransform(hide=True),
        },
    )

    result = await new_tool.run(arguments={"new_x": 3})
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "13"  # 3 + 10 (default for hidden old_y)


async def test_forward_outside_context_raises_error():
    """Test that forward() raises error when called outside transform context."""
    with pytest.raises(RuntimeError, match="forward\(\) can only be called"):
        await forward(x=1)


async def test_forward_raw_outside_context_raises_error():
    """Test that forward_raw() raises error when called outside transform context."""
    with pytest.raises(RuntimeError, match="forward_raw\(\) can only be called"):
        await forward_raw(x=1)


def test_transform_args_with_parent_defaults():
    """Test that transform_args with parent defaults works."""

    class CoolModel(BaseModel):
        x: int = 10

    def parent_tool(cool_model: CoolModel) -> int:
        return cool_model.x

    tool = Tool.from_function(parent_tool)

    new_tool = Tool.from_tool(tool)

    # Both tools should have the same dereferenced schema
    assert new_tool.parameters == tool.parameters
    # Schema should be fully dereferenced (no $defs)
    assert "$defs" not in new_tool.parameters


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
        assert add_tool is not None
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
