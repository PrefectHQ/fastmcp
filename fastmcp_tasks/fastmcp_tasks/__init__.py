"""Background task execution for FastMCP via the SEP-2663 tasks extension."""

from importlib.metadata import PackageNotFoundError, version

from fastmcp.client.extension_hooks import register_internal_client_extension_factory
from fastmcp_tasks.client import ToolTask, _build_tasks_client_extension, call_tool_task
from fastmcp_tasks.extension import TasksExtension

try:
    __version__ = version("fastmcp-tasks")
except PackageNotFoundError:
    __version__ = "0.0.0"

# Register the client half so every FastMCP `Client` transparently drives a
# task-serving backend's background tasks (see `fastmcp_tasks.client`). Importing
# this package — which any task deployment does, server or client side — is what
# turns on client task support.
register_internal_client_extension_factory(_build_tasks_client_extension)

__all__ = ["TasksExtension", "ToolTask", "call_tool_task", "__version__"]
