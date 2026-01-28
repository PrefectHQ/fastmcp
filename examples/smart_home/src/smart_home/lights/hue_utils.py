from typing import Any

from phue import Bridge
from phue.exceptions import PhueException

from smart_home.settings import settings


def _get_bridge() -> Bridge | None:
    """Attempts to connect to the Hue bridge using settings."""
    try:
        return Bridge(
            ip=settings.hue_bridge_ip,
            username=settings.hue_bridge_username,
            save_config=False,
        )
    except Exception:
        # Broad exception to catch potential connection issues
        # TODO: Add more specific logging or error handling
        return None


def handle_phue_error(
    light_or_group: str, operation: str, error: Exception
) -> dict[str, Any]:
    """Creates a standardized error response for phue operations."""
    base_info = {"target": light_or_group, "operation": operation, "success": False}
    if isinstance(error, KeyError):
        base_info["error"] = f"Target '{light_or_group}' not found"
    elif isinstance(error, PhueException):
        base_info["error"] = f"phue error during {operation}: {error}"
    else:
        base_info["error"] = f"Unexpected error during {operation}: {error}"
    return base_info
