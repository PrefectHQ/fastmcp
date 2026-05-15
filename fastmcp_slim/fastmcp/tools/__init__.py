import sys

from .function_tool import FunctionTool, tool
from .base import Tool, ToolResult
from .tool_transform import forward, forward_raw

# Backward compat: tool.py was renamed to base.py to stop Pyright from resolving
# `from fastmcp.tools import tool` as the submodule instead of the decorator function.
# This shim keeps `from fastmcp.tools.tool import Tool` working at runtime.
# Safe to remove once we're confident no external code imports from the old path.
sys.modules[f"{__name__}.tool"] = sys.modules[f"{__name__}.base"]

__all__ = [
    "FunctionTool",
    "Tool",
    "ToolResult",
    "forward",
    "forward_raw",
    "tool",
]
