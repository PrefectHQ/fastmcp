"""Task routing helper for MCP components.

Provides unified task mode enforcement and docket routing logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import mcp.types
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND, ErrorData

from fastmcp.server.dependencies import get_task_metadata
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
    key: str,
    arguments: dict[str, Any] | None = None,
) -> mcp.types.CreateTaskResult | None:
    """Check task mode and submit to background if requested.

    Args:
        component: The MCP component
        task_type: Type of task ("tool", "resource", "template", "prompt")
        key: Docket registration key (caller resolves from contextvar + fallback)
        arguments: Arguments for tool/prompt/template execution

    Returns:
        CreateTaskResult if submitted to docket, None for sync execution

    Raises:
        McpError: If mode="required" but no task metadata, or mode="forbidden"
                  but task metadata is present
    """
    task_meta = get_task_metadata()
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
    if task_config.mode == "forbidden" and task_meta:
        raise McpError(
            ErrorData(
                code=METHOD_NOT_FOUND,
                message=f"{entity_label} does not support task-augmented execution",
            )
        )

    # No task metadata - synchronous execution
    if not task_meta:
        return None

    return await submit_to_docket(task_type, key, component, arguments)
