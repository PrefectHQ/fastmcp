"""SEP-1686 task capabilities declaration."""

from mcp_types import (
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

    Only tools are advertised as task-capable. In the SDK v2 b1 wire types,
    ``ReadResourceRequestParams`` / ``GetPromptRequestParams`` carry no ``task``
    field (sdk-feedback #3), so resource/prompt task submissions are not
    wire-expressible and always graceful-degrade to synchronous execution.
    Advertising ``prompts``/``resources`` task support would mislead
    capability-discovering clients into sending task-augmented reads/gets that
    silently run synchronously. Restore them here once the SDK adds task
    metadata to those request params.
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
        ),
    )
