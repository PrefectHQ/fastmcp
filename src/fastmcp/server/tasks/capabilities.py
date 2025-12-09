"""SEP-1686 task capabilities declaration."""

from typing import Any


def get_task_capabilities() -> dict[str, Any]:
    """Return the SEP-1686 task capabilities structure.

    This is the standard capabilities map advertised to clients,
    declaring support for list, cancel, and request operations.
    """
    return {
        "tasks": {
            "list": {},
            "cancel": {},
            "requests": {
                "tools": {"call": {}},
                "prompts": {"get": {}},
                "resources": {"read": {}},
            },
        }
    }
