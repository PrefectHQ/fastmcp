import sys

from .function_tool import FunctionTool, tool
from .base import Tool, ToolResult
from .tool_transform import forward, forward_raw

# Preserve the old import path (fastmcp.tools.tool) for backward compatibility.
# The module was renamed to base.py to avoid shadowing the `tool` decorator function,
# which caused Pyright to report "Module is not callable" errors.
sys.modules[f"{__name__}.tool"] = sys.modules[f"{__name__}.base"]

__all__ = [
    "FunctionTool",
    "Tool",
    "ToolResult",
    "forward",
    "forward_raw",
    "tool",
]
