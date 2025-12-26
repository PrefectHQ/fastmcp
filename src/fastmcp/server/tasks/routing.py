"""Task routing helper for MCP components.

Provides unified task mode enforcement and docket routing logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND, ErrorData

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
    arguments: dict[str, Any] | None = None,
    task_meta: TaskMeta | None = None,
) -> mcp.types.CreateTaskResult | None:
    """Check task mode and submit to background if requested.

    Args:
        component: The MCP component
        task_type: Type of task ("tool", "resource", "template", "prompt")
        arguments: Arguments for tool/prompt/template execution
        task_meta: Task execution metadata. If provided, execute as background task.

    Returns:
        CreateTaskResult if submitted to docket, None for sync execution

    Raises:
        McpError: If mode="required" but no task metadata, or mode="forbidden"
                  but task metadata is present
    """
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

    # fn_key is expected to be set; fall back to component.key for direct calls
    fn_key = task_meta.fn_key or component.key
    return await submit_to_docket(task_type, fn_key, component, arguments, task_meta)
