"""Backward-compatible exports for component authorization primitives."""

from fastmcp.utilities.authorization import (
    AuthCheck,
    AuthContext,
    require_scopes,
    restrict_tag,
    run_auth_checks,
)

__all__ = [
    "AuthCheck",
    "AuthContext",
    "require_scopes",
    "restrict_tag",
    "run_auth_checks",
]
