"""TaskConfig for MCP SEP-1686 background task execution modes.

This module defines the configuration for how tools, resources, and prompts
handle task-augmented execution as specified in SEP-1686.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

# Task execution modes per SEP-1686 / MCP ToolExecution.taskSupport
TaskMode = Literal["forbidden", "optional", "required"]


@dataclass
class TaskConfig:
    """Configuration for MCP background task execution (SEP-1686).

    Controls how a component handles task-augmented requests:

    - "forbidden": Component does not support task execution. Clients must not
      request task augmentation; server returns -32601 if they do.
    - "optional": Component supports both synchronous and task execution.
      Client may request task augmentation or call normally.
    - "required": Component requires task execution. Clients must request task
      augmentation; server returns -32601 if they don't.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.tasks import TaskConfig

        mcp = FastMCP("MyServer")

        # Background execution required
        @mcp.tool(task=TaskConfig(mode="required"))
        async def long_running_task(): ...

        # Supports both modes (default when task=True)
        @mcp.tool(task=TaskConfig(mode="optional"))
        async def flexible_task(): ...
        ```
    """

    mode: TaskMode = "optional"

    @classmethod
    def from_bool(cls, value: bool) -> TaskConfig:
        """Convert boolean task flag to TaskConfig.

        Args:
            value: True for "optional" mode, False for "forbidden" mode.

        Returns:
            TaskConfig with appropriate mode.
        """
        return cls(mode="optional" if value else "forbidden")

    def validate_function(self, fn: Callable[..., Any], name: str) -> None:
        """Validate that function is compatible with this task config.

        Task execution requires async functions. Raises ValueError if mode
        is "optional" or "required" but function is synchronous.

        Args:
            fn: The function to validate (handles callable classes and staticmethods).
            name: Name for error messages.

        Raises:
            ValueError: If task execution is enabled but function is sync.
        """
        if self.mode == "forbidden":
            return

        # Unwrap callable classes and staticmethods
        fn_to_check = fn
        if not inspect.isroutine(fn) and callable(fn):
            fn_to_check = fn.__call__
        if isinstance(fn_to_check, staticmethod):
            fn_to_check = fn_to_check.__func__

        if not inspect.iscoroutinefunction(fn_to_check):
            raise ValueError(
                f"'{name}' uses a sync function but has task execution enabled. "
                "Background tasks require async functions."
            )
