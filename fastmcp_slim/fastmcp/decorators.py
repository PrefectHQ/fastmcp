"""Shared decorator utilities for FastMCP."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastmcp.prompts.function_prompt import PromptMeta
    from fastmcp.resources.function_resource import ResourceMeta
    from fastmcp.server.tasks.config import TaskConfig
    from fastmcp.tools.function_tool import ToolMeta

    FastMCPMeta = ToolMeta | ResourceMeta | PromptMeta


def resolve_task_config(task: bool | TaskConfig | None) -> bool | TaskConfig:
    """Resolve task config, defaulting None to False."""
    return task if task is not None else False


@runtime_checkable
class HasFastMCPMeta(Protocol):
    """Protocol for callables decorated with FastMCP metadata."""

    __fastmcp__: Any


def get_fastmcp_meta(fn: Any) -> Any | None:
    """Extract FastMCP metadata from a function, handling bound methods and wrappers."""
    if hasattr(fn, "__fastmcp__"):
        return fn.__fastmcp__
    if hasattr(fn, "__func__") and hasattr(fn.__func__, "__fastmcp__"):
        return fn.__func__.__fastmcp__
    try:
        unwrapped = inspect.unwrap(fn)
        if unwrapped is not fn and hasattr(unwrapped, "__fastmcp__"):
            return unwrapped.__fastmcp__
    except ValueError:
        pass
    return None
