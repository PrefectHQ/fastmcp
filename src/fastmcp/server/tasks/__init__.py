"""MCP SEP-1686 background tasks support.

This module implements protocol-level background task execution for MCP servers.
"""

from fastmcp.server.tasks.converters import (
    convert_prompt_result,
    convert_resource_result,
    convert_tool_result,
)
from fastmcp.server.tasks.handlers import (
    handle_prompt_as_task,
    handle_resource_as_task,
    handle_tool_as_task,
)
from fastmcp.server.tasks.keys import (
    build_task_key,
    get_client_task_id_from_key,
    parse_task_key,
)
from fastmcp.server.tasks.protocol import (
    tasks_cancel_handler,
    tasks_get_handler,
    tasks_list_handler,
    tasks_result_handler,
)

__all__ = [
    "build_task_key",
    "convert_prompt_result",
    "convert_resource_result",
    "convert_tool_result",
    "get_client_task_id_from_key",
    "handle_prompt_as_task",
    "handle_resource_as_task",
    "handle_tool_as_task",
    "parse_task_key",
    "tasks_cancel_handler",
    "tasks_get_handler",
    "tasks_list_handler",
    "tasks_result_handler",
]
