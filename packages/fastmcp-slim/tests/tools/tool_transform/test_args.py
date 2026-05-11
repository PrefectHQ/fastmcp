"""Tests for argument transformation in tool transforms."""

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from mcp.types import TextContent
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from fastmcp import FastMCP
from fastmcp.client.client import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, forward, forward_raw
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool_transform import (
    ArgTransform,
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
        (str, "string"),
        (float, "number"),
        (bool, "boolean"),
        (MyModel, "object"),
        (MyDataclass, "object"),
        (MyTypedDict, "object"),
    ],
)
def test_arg_transform_type_handling(add_tool, py_type, json_type):
    new_tool = Tool.from_tool(
        add_tool, transform_args={"old_x": ArgTransform(type=py_type)}
    )
    prop = get_property(new_tool, "old_x")
    assert prop["type"] == json_type


def test_arg_transform_annotated_types(add_tool):
    new_tool = Tool.from_tool(
        add_tool,
        transform_args={
            "old_x": ArgTransform(
                type=Annotated[int, Field(ge=0, le=100)], description="A number 0-100"
            )
        },
    )
    prop = get_property(new_tool, "old_x")
    assert prop["type"] == "integer"
    assert prop["description"] == "A number 0-100"
    assert prop["minimum"] == 0
    assert prop["maximum"] == 100


def test_arg_transform_precedence_over_function_without_kwargs():
    def base(x: int) -> int:
        return x

    tool = Tool.from_function(base)
    new_tool = Tool.from_tool(
        tool, transform_args={"x": ArgTransform(type=str, description="String input")}
    )

    prop = get_property(new_tool, "x")
    assert prop["type"] == "string"
    assert prop["description"] == "String input"


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


def test_arg_transform_combined_attributes(add_tool):
    new_tool = Tool.from_tool(
        add_tool,
        transform_args={
            "old_x": ArgTransform(
                name="new_x",
                description="New description",
                type=str,
            )
        },
    )

    prop = get_property(new_tool, "new_x")
    assert prop["type"] == "string"
    assert prop["description"] == "New description"
    assert "old_x" not in new_tool.parameters["properties"]


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


async def test_arg_transform_default_factory():
    """Test ArgTransform with default_factory for hidden parameters."""
    import asyncio
    import time

    @Tool.from_function
    def base_tool(x: int, timestamp: float) -> str:
        return f"{x}_{timestamp}"

    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "timestamp": ArgTransform(hide=True, default_factory=time.time)
        },
    )

    result1 = await new_tool.run(arguments={"x": 1})
    await asyncio.sleep(0.01)
    result2 = await new_tool.run(arguments={"x": 2})

    # Each call should get a different timestamp
    assert isinstance(result1.content[0], TextContent)
    assert isinstance(result2.content[0], TextContent)
    assert result1.content[0].text != result2.content[0].text
    assert "1_" in result1.content[0].text
    assert "2_" in result2.content[0].text


async def test_arg_transform_default_factory_called_each_time():
    """Test that default_factory is called for each tool execution."""

    call_count = {"count": 0}

    def get_counter():
        call_count["count"] += 1
        return call_count["count"]

    @Tool.from_function
    def base_tool(x: int, counter: int) -> str:
        return f"{x}_{counter}"

    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "counter": ArgTransform(hide=True, default_factory=get_counter)
        },
    )

    result1 = await new_tool.run(arguments={"x": 1})
    result2 = await new_tool.run(arguments={"x": 2})
    result3 = await new_tool.run(arguments={"x": 3})

    # Each call should increment the counter
    assert isinstance(result1.content[0], TextContent)
    assert isinstance(result2.content[0], TextContent)
    assert isinstance(result3.content[0], TextContent)
    assert "1_1" in result1.content[0].text
    assert "2_2" in result2.content[0].text
    assert "3_3" in result3.content[0].text


async def test_arg_transform_hidden_with_default_factory():
    """Test that hidden parameters with default_factory work correctly."""

    @Tool.from_function
    def base_tool(x: int, session_id: str) -> str:
        return f"{x}_{session_id}"

    import uuid

    new_tool = Tool.from_tool(
        base_tool,
        transform_args={
            "session_id": ArgTransform(
                hide=True, default_factory=lambda: str(uuid.uuid4())
            )
        },
    )

    result = await new_tool.run(arguments={"x": 1})
    # Should have a UUID in the result
    assert isinstance(result.content[0], TextContent)
    assert "1_" in result.content[0].text
    assert len(result.content[0].text.split("_")[1]) > 10


async def test_arg_transform_default_and_factory_raises_error():
    """Test that providing both default and default_factory raises an error."""
    with pytest.raises(
        ValueError, match="Cannot specify both 'default' and 'default_factory'"
    ):
        ArgTransform(default=10, default_factory=lambda: 20)


async def test_arg_transform_default_factory_requires_hide():
    """Test that default_factory requires hide=True."""
    with pytest.raises(
        ValueError, match="default_factory can only be used with hide=True"
    ):
        ArgTransform(default_factory=lambda: 10)


async def test_arg_transform_required_true(add_tool):
    """Test ArgTransform with required=True."""
    new_tool = Tool.from_tool(
        add_tool,
        transform_args={"old_y": ArgTransform(required=True)},
    )

    # old_y should now be required (even though it had a default)
    assert "old_y" in new_tool.parameters["required"]


async def test_arg_transform_required_false():
    """Test ArgTransform with required=False by setting a default."""

    def func(x: int, y: int) -> int:
        return x + y

    tool = Tool.from_function(func)
    # Setting a default makes it not required
    new_tool = Tool.from_tool(tool, transform_args={"y": ArgTransform(default=0)})

    # y should not be required since it has a default
    assert "y" not in new_tool.parameters.get("required", [])


async def test_arg_transform_required_with_rename(add_tool):
    """Test ArgTransform with required and rename."""
    new_tool = Tool.from_tool(
        add_tool,
        transform_args={"old_y": ArgTransform(name="new_y", required=True)},
    )

    # new_y should be required
    assert "new_y" in new_tool.parameters["required"]
    assert "old_y" not in new_tool.parameters["properties"]


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
    """Test that not specifying required doesn't change existing required status."""

    def func(x: int, y: int) -> int:
        return x + y

    tool = Tool.from_function(func)
    # Both x and y are required in original
    assert "x" in tool.parameters["required"]
    assert "y" in tool.parameters["required"]

    # Not specifying required should keep x required
    new_tool = Tool.from_tool(
        tool, transform_args={"x": ArgTransform(description="Updated x")}
    )

    # x should still be required, and y should still be
    assert "x" in new_tool.parameters.get("required", [])
    assert "y" in new_tool.parameters["required"]


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

        # Get the registered Tool object from the server
        add_tool = await mcp._local_provider.get_tool("add")
        assert isinstance(add_tool, Tool)
        new_add = Tool.from_tool(add_tool, name="new_add")
        mcp.add_tool(new_add)

        # Disable original tool, but new_add should still work
        mcp.disable(names={"add"}, components={"tool"})

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

        # Get the registered Tool object from the server
        add_tool = await mcp._local_provider.get_tool("add")
        assert isinstance(add_tool, Tool)
        new_add = Tool.from_tool(add_tool, name="new_add")
        mcp.add_tool(new_add)

        # Disable both tools via server
        mcp.disable(names={"add"}, components={"tool"}).disable(
            names={"new_add"}, components={"tool"}
        )

        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert len(tools) == 0

            with pytest.raises(ToolError):
                await client.call_tool("new_add", {"x": 1, "y": 2})
