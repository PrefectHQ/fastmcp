"""MCP SEP-1686 background tasks support.

This module implements protocol-level background task execution for MCP servers.
"""

from fastmcp.server.tasks.capabilities import get_task_capabilities
from fastmcp.server.tasks.config import TaskConfig, TaskMeta, TaskMode
from fastmcp.server.tasks.elicitation import elicit_for_task, handle_task_input
from fastmcp.server.tasks.keys import (
    build_task_key,
    get_client_task_id_from_key,
    parse_task_key,
)

__all__ = [
    "TaskConfig",
    "TaskMeta",
    "TaskMode",
    "build_task_key",
    "elicit_for_task",
    "get_client_task_id_from_key",
    "get_task_capabilities",
    "handle_task_input",
    "parse_task_key",
]
