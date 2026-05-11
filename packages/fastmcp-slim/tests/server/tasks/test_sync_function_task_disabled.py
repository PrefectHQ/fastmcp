"""
Tests that synchronous functions cannot be used as background tasks.

Docket requires async functions for background execution. FastMCP raises
ValueError when task=True is used with a sync function.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.prompts.function_prompt import FunctionPrompt
from fastmcp.resources.function_resource import FunctionResource
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


async def test_sync_prompt_with_explicit_task_true_raises():
    """Sync prompt with task=True raises ValueError."""
    mcp = FastMCP("test")

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.prompt(task=True)
        def sync_prompt() -> str:
            """A synchronous prompt."""
            return "Hello"


async def test_sync_prompt_with_inherited_task_true_raises():
    """Sync prompt inheriting task=True from server raises ValueError."""
    mcp = FastMCP("test", tasks=True)

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.prompt()  # Inherits task=True from server
        def sync_prompt() -> str:
            """A synchronous prompt."""
            return "Hello"


async def test_sync_resource_with_explicit_task_true_raises():
    """Sync resource with task=True raises ValueError."""
    mcp = FastMCP("test")

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.resource("test://sync", task=True)
        def sync_resource() -> str:
            """A synchronous resource."""
            return "data"


async def test_sync_resource_with_inherited_task_true_raises():
    """Sync resource inheriting task=True from server raises ValueError."""
    mcp = FastMCP("test", tasks=True)

    with pytest.raises(
        ValueError, match="uses a sync function but has task execution enabled"
    ):

        @mcp.resource("test://sync")  # Inherits task=True from server
        def sync_resource() -> str:
            """A synchronous resource."""
            return "data"


async def test_async_tool_with_task_true_remains_enabled():
    """Async tools with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def async_tool(x: int) -> int:
        """An async tool."""
        return x * 2

    # Tool should have task mode="optional" and be a FunctionTool
    tool = await mcp.get_tool("async_tool")
    assert isinstance(tool, FunctionTool)
    assert tool.task_config.mode == "optional"


async def test_async_prompt_with_task_true_remains_enabled():
    """Async prompts with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.prompt(task=True)
    async def async_prompt() -> str:
        """An async prompt."""
        return "Hello"

    # Prompt should have task mode="optional" and be a FunctionPrompt
    prompt = await mcp.get_prompt("async_prompt")
    assert isinstance(prompt, FunctionPrompt)
    assert prompt.task_config.mode == "optional"


async def test_async_resource_with_task_true_remains_enabled():
    """Async resources with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.resource("test://async", task=True)
    async def async_resource() -> str:
        """An async resource."""
        return "data"

    # Resource should have task mode="optional" and be a FunctionResource
    resource = await mcp.get_resource("test://async")
    assert isinstance(resource, FunctionResource)
    assert resource.task_config.mode == "optional"


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


async def test_sync_prompt_with_task_false_works():
    """Sync prompt with explicit task=False works (no error)."""
    mcp = FastMCP("test", tasks=True)

    @mcp.prompt(task=False)  # Explicitly disable
    def sync_prompt() -> str:
        """A synchronous prompt."""
        return "Hello"

    prompt = await mcp.get_prompt("sync_prompt")
    assert isinstance(prompt, FunctionPrompt)
    assert prompt.task_config.mode == "forbidden"


async def test_sync_resource_with_task_false_works():
    """Sync resource with explicit task=False works (no error)."""
    mcp = FastMCP("test", tasks=True)

    @mcp.resource("test://sync", task=False)  # Explicitly disable
    def sync_resource() -> str:
        """A synchronous resource."""
        return "data"

    resource = await mcp.get_resource("test://sync")
    assert isinstance(resource, FunctionResource)
    assert resource.task_config.mode == "forbidden"


# =============================================================================
# Callable classes and staticmethods with async __call__
# =============================================================================


async def test_async_callable_class_tool_with_task_true_works():
    """Callable class with async __call__ and task=True should work."""
    from fastmcp.tools import Tool

    class AsyncCallableTool:
        async def __call__(self, x: int) -> int:
            return x * 2

    # Callable classes use Tool.from_function() directly
    tool = Tool.from_function(AsyncCallableTool(), task=True)
    assert tool.task_config.mode == "optional"


async def test_async_callable_class_prompt_with_task_true_works():
    """Callable class with async __call__ and task=True should work."""
    from fastmcp.prompts import Prompt

    class AsyncCallablePrompt:
        async def __call__(self) -> str:
            return "Hello"

    # Callable classes use Prompt.from_function() directly
    prompt = Prompt.from_function(AsyncCallablePrompt(), task=True)
    assert prompt.task_config.mode == "optional"


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
