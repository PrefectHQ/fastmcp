"""Server-wide ``tasks`` default applies to ``add_tool()`` and the ``tools=`` constructor parameter.

Tools registered through ``@mcp.tool`` already inherited the server-wide
``FastMCP(tasks=...)`` default; tools registered through any other path were
silently pinned to ``forbidden``. These tests lock in that all function
registration paths behave the same (issue #5782).
"""

from __future__ import annotations

import pytest

from fastmcp import FastMCP
from fastmcp.tools import Tool
from fastmcp.tools import tool as standalone_tool


async def _async_fn() -> str:
    return "ok"


def _sync_fn() -> str:
    return "ok"


async def test_add_tool_inherits_server_tasks_default():
    """A plain function added via add_tool() inherits tasks=True as optional."""
    mcp = FastMCP("test", tasks=True)
    mcp.add_tool(_async_fn)
    tool = await mcp.get_tool("_async_fn")
    assert tool.task_config.mode == "optional"


async def test_tools_constructor_param_inherits_server_tasks_default():
    """A plain function passed via tools=[...] inherits tasks=True as optional."""
    mcp = FastMCP("test", tasks=True, tools=[_async_fn])
    tool = await mcp.get_tool("_async_fn")
    assert tool.task_config.mode == "optional"


async def test_add_tool_standalone_decorator_inherits_server_tasks_default():
    """A function with the standalone @tool decorator (no task=) inherits the default."""

    @standalone_tool
    async def my_tool() -> str:
        return "ok"

    mcp = FastMCP("test", tasks=True)
    mcp.add_tool(my_tool)
    tool = await mcp.get_tool("my_tool")
    assert tool.task_config.mode == "optional"


async def test_add_tool_explicit_task_false_is_not_overridden():
    """An explicit task=False via the standalone decorator wins over the default."""

    @standalone_tool(task=False)
    async def my_tool() -> str:
        return "ok"

    mcp = FastMCP("test", tasks=True)
    mcp.add_tool(my_tool)
    tool = await mcp.get_tool("my_tool")
    assert tool.task_config.mode == "forbidden"


async def test_add_tool_prebuilt_tool_instance_is_not_overridden():
    """An already-constructed Tool keeps its own task_config (explicit)."""
    mcp = FastMCP("test", tasks=True)
    mcp.add_tool(Tool.from_function(_async_fn))
    tool = await mcp.get_tool("_async_fn")
    assert tool.task_config.mode == "forbidden"


async def test_add_tool_sync_function_raises_like_decorator():
    """add_tool() of a sync function on a tasks=True server raises, like @mcp.tool."""
    mcp = FastMCP("test", tasks=True)
    with pytest.raises(ValueError, match="task execution enabled"):
        mcp.add_tool(_sync_fn)


async def test_add_tool_sync_function_allowed_without_tasks_default():
    """Without a tasks default, add_tool() of a sync function keeps registering."""
    mcp = FastMCP("test")
    mcp.add_tool(_sync_fn)
    tool = await mcp.get_tool("_sync_fn")
    assert tool.task_config.mode == "forbidden"
