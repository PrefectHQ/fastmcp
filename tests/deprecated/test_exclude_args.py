from typing import Any

import pytest
from mcp.server.session import ServerSession

from fastmcp import Client, FastMCP
from fastmcp.tools.tool import Tool


async def test_tool_exclude_args():
    """Test that tool args are excluded."""
    mcp = FastMCP("Test Server")

    @mcp.tool(exclude_args=["state"])
    def echo(message: str, state: dict[str, Any] | None = None) -> str:
        """Echo back the message provided."""
        if state:
            # State was read
            pass
        return message

    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert "state" not in tools[0].parameters["properties"]


async def test_tool_exclude_args_without_default_value_raises_error():
    """Test that excluding args without default values raises ValueError"""
    mcp = FastMCP("Test Server")

    with pytest.raises(ValueError):

        @mcp.tool(exclude_args=["state"])
        def echo(message: str, state: dict[str, Any] | None) -> str:
            """Echo back the message provided."""
            if state:
                # State was read
                pass
            return message


async def test_add_tool_method_exclude_args():
    """Test that tool exclude_args work with the add_tool method."""
    mcp = FastMCP("Test Server")

    def create_item(
        name: str, value: int, state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a new item."""
        if state:
            # State was read
            pass
        return {"name": name, "value": value}

    tool = Tool.from_function(
        create_item,
        name="create_item",
        exclude_args=["state"],
    )
    mcp.add_tool(tool)

    # Check tool via public API
    tools = await mcp.list_tools()
    assert len(tools) == 1
    assert "state" not in tools[0].parameters["properties"]


async def test_tool_functionality_with_exclude_args():
    """Test that tool functionality is preserved when using exclude_args."""
    mcp = FastMCP("Test Server")

    def create_item(
        name: str, value: int, state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a new item."""
        if state:
            # state was read
            pass
        return {"name": name, "value": value}

    tool = Tool.from_function(
        create_item,
        name="create_item",
        exclude_args=["state"],
    )
    mcp.add_tool(tool)

    # Use the tool to verify functionality is preserved
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_item", {"name": "test_item", "value": 42}
        )
        assert result.data == {"name": "test_item", "value": 42}


async def test_exclude_args_with_non_serializable_type():
    """Test that exclude_args works even when the excluded parameter type can't be serialized.

    This test ensures that exclude_args works correctly when the excluded parameter
    has a type that Pydantic cannot serialize (like ServerSession). The bug was that
    get_cached_typeadapter would try to serialize all parameters before compress_schema
    could exclude them, causing a PydanticSchemaGenerationError.
    """

    def my_tool(message: str, session: ServerSession | None = None) -> str:
        """A tool that takes a non-serializable Session parameter."""
        return message

    # This should not raise an error even though ServerSession can't be serialized
    tool = Tool.from_function(
        my_tool,
        name="my_tool",
        exclude_args=["session"],
    )

    # Verify the tool was created successfully
    assert tool is not None
    assert tool.name == "my_tool"

    # Verify the session parameter is excluded from the schema
    assert "session" not in tool.parameters["properties"]
    assert "message" in tool.parameters["properties"]
