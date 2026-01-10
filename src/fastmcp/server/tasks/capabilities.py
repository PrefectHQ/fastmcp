"""SEP-1686 task capabilities declaration."""

from importlib.util import find_spec
from typing import Any


def _is_docket_available() -> bool:
    """Check if pydocket is installed (local to avoid circular import)."""
    return find_spec("docket") is not None


def get_task_capabilities() -> dict[str, Any]:
    """Return the SEP-1686 task capabilities structure.

    This is the standard capabilities map advertised to clients,
    declaring support for list, cancel, and request operations.

    Returns empty dict if pydocket is not installed, so clients
    won't see task support advertised.
    """
    if not _is_docket_available():
        return {}

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
