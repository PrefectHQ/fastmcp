"""Task key management for SEP-1686 background tasks.

Task keys encode security scoping and metadata in the Docket key format:
    {session_id}:{client_task_id}:{task_type}:{component_identifier}

This format provides:
- Session-based security scoping (prevents cross-session access)
- Task type identification (tool/prompt/resource)
- Component identification (name or URI for result conversion)
"""

from urllib.parse import quote, unquote


def build_task_key(
    session_id: str,
    client_task_id: str,
    task_type: str,
    component_identifier: str,
) -> str:
    """Build Docket task key with embedded metadata.

    Format: {session_id}:{client_task_id}:{task_type}:{component_identifier}

    The component_identifier is URI-encoded to handle special characters (colons, slashes, etc.).

    Args:
        session_id: Session ID for security scoping
        client_task_id: Client-provided task ID
        task_type: Type of task ("tool", "prompt", "resource")
        component_identifier: Tool name, prompt name, or resource URI

    Returns:
        Encoded task key for Docket

    Examples:
        >>> build_task_key("session123", "task456", "tool", "my_tool")
        'session123:task456:tool:my_tool'

        >>> build_task_key("session123", "task456", "resource", "file://data.txt")
        'session123:task456:resource:file%3A%2F%2Fdata.txt'
    """
    encoded_identifier = quote(component_identifier, safe="")
    return f"{session_id}:{client_task_id}:{task_type}:{encoded_identifier}"


def parse_task_key(task_key: str) -> dict[str, str]:
    """Parse Docket task key to extract metadata.

    Args:
        task_key: Encoded task key from Docket

    Returns:
        Dict with keys: session_id, client_task_id, task_type, component_identifier

    Examples:
        >>> parse_task_key("session123:task456:tool:my_tool")
        {'session_id': 'session123', 'client_task_id': 'task456',
         'task_type': 'tool', 'component_identifier': 'my_tool'}

        >>> parse_task_key("session123:task456:resource:file%3A%2F%2Fdata.txt")
        {'session_id': 'session123', 'client_task_id': 'task456',
         'task_type': 'resource', 'component_identifier': 'file://data.txt'}
    """
    parts = task_key.split(":", 3)
    if len(parts) != 4:
        raise ValueError(
            f"Invalid task key format: {task_key}. "
            f"Expected: {{session_id}}:{{client_task_id}}:{{task_type}}:{{component_identifier}}"
        )

    return {
        "session_id": parts[0],
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
        >>> get_client_task_id_from_key("session123:task456:tool:my_tool")
        'task456'
    """
    return task_key.split(":", 3)[1]
