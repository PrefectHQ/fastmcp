"""SEP-1686 task capabilities declaration."""

from mcp.types import (
    ServerTasksCapability,
    ServerTasksRequestsCapability,
    TasksCallCapability,
    TasksCancelCapability,
    TasksListCapability,
    TasksToolsCapability,
)


def get_task_capabilities() -> ServerTasksCapability | None:
    """Return the SEP-1686 task capabilities.

    Returns task capabilities as a first-class ServerCapabilities field,
    declaring support for list, cancel, and request operations per SEP-1686.

    Returns None if a compatible pydocket is not installed (no task support).
    Uses the canonical ``is_docket_available()`` check so that capability
    advertisement and handler registration stay in sync — otherwise a server
    with an old transitive pydocket would advertise task support and then
    return "method not found" when clients invoked it.

    Note: prompts/resources are passed via extra_data since the SDK types
    don't include them yet (FastMCP supports them ahead of the spec).
    """
    # Function-local import to avoid a circular import at module load time:
    # fastmcp.server.tasks.__init__ pulls in this module, and dependencies
    # transitively reaches back into fastmcp.server.tasks.keys.
    from fastmcp.server.dependencies import is_docket_available

    if not is_docket_available():
        return None

    return ServerTasksCapability(
        list=TasksListCapability(),
        cancel=TasksCancelCapability(),
        requests=ServerTasksRequestsCapability(
            tools=TasksToolsCapability(call=TasksCallCapability()),
            prompts={"get": {}},  # type: ignore[call-arg]  # extra_data for forward compat  # ty:ignore[unknown-argument]
            resources={"read": {}},  # type: ignore[call-arg]  # extra_data for forward compat  # ty:ignore[unknown-argument]
        ),
    )
