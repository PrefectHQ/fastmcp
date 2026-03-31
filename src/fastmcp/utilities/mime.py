"""MIME type constants and helpers for MCP Apps UI resources.

This module has no dependencies on the server or resource packages,
so it can be safely imported from anywhere.
"""

UI_MIME_TYPE = "text/html;profile=mcp-app"


def resolve_ui_mime_type(uri: str, explicit_mime_type: str | None) -> str | None:
    """Return the appropriate MIME type for a resource URI.

    For ``ui://`` scheme resources, defaults to ``UI_MIME_TYPE`` when no
    explicit MIME type is provided.

    Args:
        uri: The resource URI string
        explicit_mime_type: The MIME type explicitly provided by the user

    Returns:
        The resolved MIME type (explicit value, UI default, or None)
    """
    if explicit_mime_type is not None:
        return explicit_mime_type
    if uri.lower().startswith("ui://"):
        return UI_MIME_TYPE
    return None
