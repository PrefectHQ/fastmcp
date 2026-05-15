"""Docket and Redis key encoding for background tasks.

The compound Docket task key embeds the auth boundary so that the parser can
reject cross-scope access without consulting Redis.  Authenticated and
anonymous tasks live in disjoint keyspaces:

    auth:{enc_scope}:{client_task_id}:{task_type}:{enc_identifier}
    anon:{client_task_id}:{task_type}:{enc_identifier}

The same `auth/anon` partition is used for the per-task Redis prefix
(``fastmcp:task:auth:{enc_scope}`` vs ``fastmcp:task:anon``) — see
``task_redis_prefix``.

``task_scope`` is the raw scope identifier (typically derived from
``client_id`` or ``client_id|sub``); encoding happens once, at the boundary,
in this module.
"""

from typing import TypedDict
from urllib.parse import quote, unquote


class TaskKeyParts(TypedDict):
    """Decoded segments of a Docket task key.

    ``task_scope`` is ``None`` for anonymous tasks, the raw scope string
    otherwise.
    """

    task_scope: str | None
    client_task_id: str
    task_type: str
    component_identifier: str


_AUTH_TAG = "auth"
_ANON_TAG = "anon"
_VALID_TAGS = (_AUTH_TAG, _ANON_TAG)


def build_task_key(
    task_scope: str | None,
    client_task_id: str,
    task_type: str,
    component_identifier: str,
) -> str:
    """Build Docket task key with embedded metadata.

    When ``task_scope`` is ``None`` the task is anonymous and lives in the
    ``anon`` keyspace.  Otherwise it lives under ``auth:{enc_scope}``.

    Args:
        task_scope: Raw authorization scope, or ``None`` for anonymous tasks
        client_task_id: Client-provided task ID
        task_type: Type of task ("tool", "prompt", "resource")
        component_identifier: Tool name, prompt name, or resource URI

    Returns:
        Encoded task key for Docket

    Examples:
        >>> build_task_key("client-a", "task456", "tool", "my_tool")
        'auth:client-a:task456:tool:my_tool'

        >>> build_task_key(None, "task456", "tool", "my_tool")
        'anon:task456:tool:my_tool'

        >>> build_task_key("client-a", "task456", "resource", "file://data.txt")
        'auth:client-a:task456:resource:file%3A%2F%2Fdata.txt'
    """
    encoded_identifier = quote(component_identifier, safe="")
    if task_scope is None:
        return f"{_ANON_TAG}:{client_task_id}:{task_type}:{encoded_identifier}"
    encoded_scope = quote(task_scope, safe="")
    return (
        f"{_AUTH_TAG}:{encoded_scope}:{client_task_id}:{task_type}:{encoded_identifier}"
    )


def parse_task_key(task_key: str) -> TaskKeyParts:
    """Parse Docket task key to extract metadata.

    Args:
        task_key: Encoded task key from Docket

    Returns:
        Dict with keys: ``task_scope`` (``str | None``), ``client_task_id``,
        ``task_type``, ``component_identifier``.

    Raises:
        ValueError: If the key has an unrecognized tag or wrong segment count.

    Examples:
        >>> parse_task_key("auth:client-a:task456:tool:my_tool")
        `{'task_scope': 'client-a', 'client_task_id': 'task456', 'task_type': 'tool', 'component_identifier': 'my_tool'}`

        >>> parse_task_key("anon:task456:tool:my_tool")
        `{'task_scope': None, 'client_task_id': 'task456', 'task_type': 'tool', 'component_identifier': 'my_tool'}`
    """
    tag, _, rest = task_key.partition(":")
    if tag not in _VALID_TAGS or not rest:
        raise ValueError(
            f"Invalid task key format: {task_key}. "
            f"Expected leading tag in {_VALID_TAGS}."
        )

    if tag == _ANON_TAG:
        parts = rest.split(":", 2)
        if len(parts) != 3:
            raise ValueError(
                f"Invalid anonymous task key: {task_key}. "
                f"Expected: anon:{{client_task_id}}:{{task_type}}:{{component_identifier}}"
            )
        client_task_id, task_type, encoded_identifier = parts
        return {
            "task_scope": None,
            "client_task_id": client_task_id,
            "task_type": task_type,
            "component_identifier": unquote(encoded_identifier),
        }

    parts = rest.split(":", 3)
    if len(parts) != 4:
        raise ValueError(
            f"Invalid authenticated task key: {task_key}. "
            f"Expected: auth:{{enc_scope}}:{{client_task_id}}:{{task_type}}:{{component_identifier}}"
        )
    encoded_scope, client_task_id, task_type, encoded_identifier = parts
    return {
        "task_scope": unquote(encoded_scope),
        "client_task_id": client_task_id,
        "task_type": task_type,
        "component_identifier": unquote(encoded_identifier),
    }


def get_client_task_id_from_key(task_key: str) -> str:
    """Extract just the client task ID from a task key.

    Args:
        task_key: Full encoded task key

    Returns:
        Client-provided task ID

    Examples:
        >>> get_client_task_id_from_key("auth:client-a:task456:tool:my_tool")
        'task456'

        >>> get_client_task_id_from_key("anon:task456:tool:my_tool")
        'task456'
    """
    return parse_task_key(task_key)["client_task_id"]


def task_redis_prefix(task_scope: str | None) -> str:
    """Return the Redis key prefix that owns a given scope.

    Authenticated tasks live under ``fastmcp:task:auth:{enc_scope}``;
    anonymous tasks live under ``fastmcp:task:anon``.  Callers append
    ``f":{task_id}:..."`` to compose the final key.
    """
    if task_scope is None:
        return f"fastmcp:task:{_ANON_TAG}"
    return f"fastmcp:task:{_AUTH_TAG}:{quote(task_scope, safe='')}"
