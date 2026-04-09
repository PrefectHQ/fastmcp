"""Docket key encoding for background tasks.

Builds and parses the compound string key that Docket uses to identify a task
execution::

    {task_scope}:{client_task_id}:{task_type}:{component_identifier}

Both ``task_scope`` and ``component_identifier`` are URI-encoded so that
special characters never collide with the ``:`` delimiter.  See
``context.py`` for how the ``task_scope`` value is determined.
"""

from urllib.parse import quote, unquote


def build_task_key(
    task_scope: str,
    client_task_id: str,
    task_type: str,
    component_identifier: str,
) -> str:
    """Build Docket task key with embedded metadata.

    Format: `{task_scope}:{client_task_id}:{task_type}:{component_identifier}`

    Both the task_scope and component_identifier are URI-encoded to handle
    special characters (colons, slashes, etc.).

    Args:
        task_scope: Authorization scope for security isolation
        client_task_id: Client-provided task ID
        task_type: Type of task ("tool", "prompt", "resource")
        component_identifier: Tool name, prompt name, or resource URI

    Returns:
        Encoded task key for Docket

    Examples:
        >>> build_task_key("client-a", "task456", "tool", "my_tool")
        'client-a:task456:tool:my_tool'

        >>> build_task_key("client-a", "task456", "resource", "file://data.txt")
        'client-a:task456:resource:file%3A%2F%2Fdata.txt'
    """
    encoded_scope = quote(task_scope, safe="")
    encoded_identifier = quote(component_identifier, safe="")
    return f"{encoded_scope}:{client_task_id}:{task_type}:{encoded_identifier}"


def parse_task_key(task_key: str) -> dict[str, str]:
    """Parse Docket task key to extract metadata.

    Args:
        task_key: Encoded task key from Docket

    Returns:
        Dict with keys: task_scope, client_task_id, task_type, component_identifier

    Examples:
        >>> parse_task_key("client-a:task456:tool:my_tool")
        `{'task_scope': 'client-a', 'client_task_id': 'task456', 'task_type': 'tool', 'component_identifier': 'my_tool'}`

        >>> parse_task_key("client-a:task456:resource:file%3A%2F%2Fdata.txt")
        `{'task_scope': 'client-a', 'client_task_id': 'task456', 'task_type': 'resource', 'component_identifier': 'file://data.txt'}`
    """
    parts = task_key.split(":", 3)
    if len(parts) != 4:
        raise ValueError(
            f"Invalid task key format: {task_key}. "
            f"Expected: {{task_scope}}:{{client_task_id}}:{{task_type}}:{{component_identifier}}"
        )

    return {
        "task_scope": unquote(parts[0]),
        "client_task_id": parts[1],
        "task_type": parts[2],
        "component_identifier": unquote(parts[3]),
    }


def get_client_task_id_from_key(task_key: str) -> str:
    """Extract just the client task ID from a task key.

    Args:
        task_key: Full encoded task key

    Returns:
        Client-provided task ID (second segment)

    Example:
        >>> get_client_task_id_from_key("client-a:task456:tool:my_tool")
        'task456'
    """
    return task_key.split(":", 3)[1]
