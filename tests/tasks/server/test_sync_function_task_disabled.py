"""
Tests that synchronous functions cannot be used as background tasks.

SEP-2663 tasks are tools-only. Docket requires async functions for background
execution, so FastMCP raises ValueError when task=True is used with a sync tool
function. These are registration-time checks and need no running server.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool


async def test_sync_tool_with_explicit_task_true_raises():
    """Sync tool with task=True raises ValueError."""
    mcp = FastMCP("test")

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.tool(task=True)
        def sync_tool(x: int) -> int:
            """A synchronous tool."""
            return x * 2


async def test_sync_tool_with_inherited_task_true_raises():
    """Sync tool inheriting task=True from server raises ValueError."""
    mcp = FastMCP("test", tasks=True)

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.tool()  # Inherits task=True from server
        def sync_tool(x: int) -> int:
            """A synchronous tool."""
            return x * 2


async def test_async_tool_with_task_true_remains_enabled():
    """Async tools with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def async_tool(x: int) -> int:
        """An async tool."""
        return x * 2

    tool = await mcp.get_tool("async_tool")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "optional"


async def test_sync_tool_with_task_false_works():
    """Sync tool with explicit task=False works (no error)."""
    mcp = FastMCP("test", tasks=True)

    @mcp.tool(task=False)  # Explicitly disable
    def sync_tool(x: int) -> int:
        """A synchronous tool."""
        return x * 2

    tool = await mcp.get_tool("sync_tool")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "forbidden"


# =============================================================================
# Callable classes with async __call__
# =============================================================================


async def test_async_callable_class_tool_with_task_true_works():
    """Callable class with async __call__ and task=True should work."""
    from fastmcp.tools import Tool

    class AsyncCallableTool:
        async def __call__(self, x: int) -> int:
            return x * 2

    tool = Tool.from_function(AsyncCallableTool(), task=True)
    assert tool.task_config.mode == "optional"


async def test_sync_callable_class_tool_with_task_true_raises():
    """Callable class with sync __call__ and task=True should raise."""
    from fastmcp.tools import Tool

    class SyncCallableTool:
        def __call__(self, x: int) -> int:
            return x * 2

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):
        Tool.from_function(SyncCallableTool(), task=True)
