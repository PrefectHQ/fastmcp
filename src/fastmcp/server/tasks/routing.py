"""Task routing helper for MCP components.

Provides unified task mode enforcement and docket routing logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND, ErrorData

from fastmcp.server.dependencies import get_task_metadata
from fastmcp.server.tasks.config import TaskMeta
from fastmcp.server.tasks.handlers import submit_to_docket

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool

TaskType = Literal["tool", "resource", "template", "prompt"]


async def check_background_task(
    component: Tool | Resource | ResourceTemplate | Prompt,
    task_type: TaskType,
    # TODO: Remove `key` parameter when resources and prompts are updated to use
    # explicit task_meta parameter like tools do
    key: str | None = None,
    arguments: dict[str, Any] | None = None,
    task_meta: TaskMeta | None = None,
) -> mcp.types.CreateTaskResult | None:
    """Check task mode and submit to background if requested.

    Args:
        component: The MCP component
        task_type: Type of task ("tool", "resource", "template", "prompt")
        key: Docket registration key (deprecated, use task_meta.fn_key instead)
        arguments: Arguments for tool/prompt/template execution
        task_meta: Task execution metadata. If provided, execute as background task.
            When None, falls back to reading from contextvar for backwards compat.

    Returns:
        CreateTaskResult if submitted to docket, None for sync execution

    Raises:
        McpError: If mode="required" but no task metadata, or mode="forbidden"
                  but task metadata is present
    """
    # For backwards compatibility: if task_meta not provided, check contextvar
    # This is used by resources/prompts which haven't been updated yet
    if task_meta is None:
        task_meta_dict = get_task_metadata()
        if task_meta_dict is not None:
            task_meta = TaskMeta(
                ttl=task_meta_dict.get("ttl"),
                fn_key=key,  # Use key parameter for backwards compat
            )

    task_config = component.task_config

    # Infer label from component
    entity_label = f"{type(component).__name__} '{component.title or component.key}'"

    # Enforce mode="required" - must have task metadata
    if task_config.mode == "required" and not task_meta:
        raise McpError(
            ErrorData(
                code=METHOD_NOT_FOUND,
                message=f"{entity_label} requires task-augmented execution",
            )
        )

    # Enforce mode="forbidden" - cannot be called with task metadata
    if not task_config.supports_tasks() and task_meta:
        raise McpError(
            ErrorData(
                code=METHOD_NOT_FOUND,
                message=f"{entity_label} does not support task-augmented execution",
            )
        )

    # No task metadata - synchronous execution
    if not task_meta:
        return None

    # fn_key should be set by caller (FastMCP.call_tool enriches it)
    # Fall back to key parameter for backwards compat, then component.key
    fn_key = task_meta.fn_key or key or component.key
    return await submit_to_docket(task_type, fn_key, component, arguments, task_meta)
