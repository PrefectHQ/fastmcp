from typing import Any

import pytest
from mcp.types import TextContent

from fastmcp import Client, FastMCP


async def test_tool_exclude_args_in_tool_manager():
    """Test that tool args are excluded in the tool manager."""
    mcp = FastMCP("Test Server")

    @mcp.tool(exclude_args=["state"])
    def echo(message: str, state: dict[str, Any] | None = None) -> str:
        """Echo back the message provided."""
        if state:
            # State was read
            pass
        return message

    tools = mcp._tool_manager.list_tools()
    assert len(tools) == 1
    assert tools[0].exclude_args is not None
    for args in tools[0].exclude_args:
        assert args not in tools[0].parameters


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

    mcp.add_tool(create_item, name="create_item", exclude_args=["state"])

    # Check internal tool objects directly
    tools = mcp._tool_manager.list_tools()
    assert len(tools) == 1
    assert tools[0].exclude_args is not None
    assert tools[0].exclude_args == ["state"]
    for args in tools[0].exclude_args:
        assert args not in tools[0].parameters


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

    mcp.add_tool(create_item, name="create_item", exclude_args=["state"])

    # Use the tool to verify functionality is preserved
    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_item", {"name": "test_item", "value": 42}
        )
        assert len(result) == 1
        assert isinstance(result[0], TextContent)

        # The result should contain the expected JSON
        assert '"name": "test_item"' in result[0].text
        assert '"value": 42' in result[0].text
