"""Backward-compatible exports for component authorization primitives."""

from fastmcp.utilities.authorization import (
    AuthCheck,
    AuthContext,
    require_scopes,
    restrict_tag,
    run_auth_checks,
    run_auth_checks_with_shortfall,
)

__all__ = [
    "AuthCheck",
    "AuthContext",
    "require_scopes",
    "restrict_tag",
    "run_auth_checks",
    "run_auth_checks_with_shortfall",
]
