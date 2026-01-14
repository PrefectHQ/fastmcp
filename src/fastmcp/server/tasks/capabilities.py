"""SEP-1686 task capabilities declaration."""

from importlib.util import find_spec

from mcp.types import (
    ServerTasksCapability,
    ServerTasksRequestsCapability,
    TasksCallCapability,
    TasksCancelCapability,
    TasksListCapability,
    TasksToolsCapability,
)


def _is_docket_available() -> bool:
    """Check if pydocket is installed (local to avoid circular import)."""
    return find_spec("docket") is not None


def get_task_capabilities() -> ServerTasksCapability | None:
    """Return the SEP-1686 task capabilities.

    Returns task capabilities as a first-class ServerCapabilities field,
    declaring support for list, cancel, and request operations per SEP-1686.

    Returns None if pydocket is not installed (no task support).

    Note: prompts/resources are passed via extra_data since the SDK types
    don't include them yet (FastMCP supports them ahead of the spec).
    """
    if not _is_docket_available():
        return None

    return ServerTasksCapability(
        list=TasksListCapability(),
        cancel=TasksCancelCapability(),
        requests=ServerTasksRequestsCapability(
            tools=TasksToolsCapability(call=TasksCallCapability()),
            prompts={"get": {}},  # type: ignore[call-arg]  # extra_data for forward compat
            resources={"read": {}},  # type: ignore[call-arg]  # extra_data for forward compat
        ),
    )
