"""Authorization checks for FastMCP components.

This module provides callable-based authorization for tools, resources, and prompts.
Auth checks are functions that receive an AuthContext and return True to allow access
or False to deny.

Auth checks can also raise exceptions:
- AuthorizationError: Propagates with the custom message for explicit denial
- Other exceptions: Masked for security (logged, treated as auth failure)

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.auth import require_auth, require_scopes

    mcp = FastMCP()

    @mcp.tool(auth=require_auth)
    def protected_tool(): ...

    @mcp.resource("data://secret", auth=require_scopes("read"))
    def secret_data(): ...

    @mcp.prompt(auth=require_auth)
    def admin_prompt(): ...
    ```
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastmcp.exceptions import AuthorizationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken
    from fastmcp.tools.tool import Tool
    from fastmcp.utilities.components import FastMCPComponent


@dataclass
class AuthContext:
    """Context passed to auth check callables.

    This object is passed to each auth check function and provides
    access to the current authentication token and the component being accessed.

    Attributes:
        token: The current access token, or None if unauthenticated.
        component: The component (tool, resource, or prompt) being accessed.
        tool: Backwards-compatible alias for component when it's a Tool.
    """

    token: AccessToken | None
    component: FastMCPComponent

    @property
    def tool(self) -> Tool | None:
        """Backwards-compatible access to the component as a Tool.

        Returns the component if it's a Tool, None otherwise.
        """
        from fastmcp.tools.tool import Tool

        return self.component if isinstance(self.component, Tool) else None


# Type alias for auth check functions
AuthCheck = Callable[[AuthContext], bool]


def require_auth(ctx: AuthContext) -> bool:
    """Require any valid authentication.

    Returns True if the request has a valid token, False otherwise.

    Example:
        ```python
        @mcp.tool(auth=require_auth)
        def protected_tool(): ...
        ```
    """
    return ctx.token is not None


def require_scopes(*scopes: str) -> AuthCheck:
    """Require specific OAuth scopes.

    Returns an auth check that requires ALL specified scopes to be present
    in the token (AND logic).

    Args:
        *scopes: One or more scope strings that must all be present.

    Example:
        ```python
        @mcp.tool(auth=require_scopes("admin"))
        def admin_tool(): ...

        @mcp.tool(auth=require_scopes("read", "write"))
        def read_write_tool(): ...
        ```
    """
    required = set(scopes)

    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        return required.issubset(set(ctx.token.scopes))

    return check


def restrict_tag(tag: str, *, scopes: list[str]) -> AuthCheck:
    """Restrict components with a specific tag to require certain scopes.

    If the component has the specified tag, the token must have ALL the
    required scopes. If the component doesn't have the tag, access is allowed.

    Args:
        tag: The tag that triggers the scope requirement.
        scopes: List of scopes required when the tag is present.

    Example:
        ```python
        # Components tagged "admin" require the "admin" scope
        AuthMiddleware(auth=restrict_tag("admin", scopes=["admin"]))
        ```
    """
    required = set(scopes)

    def check(ctx: AuthContext) -> bool:
        if tag not in ctx.component.tags:
            return True  # Tag not present, no restriction
        if ctx.token is None:
            return False
        return required.issubset(set(ctx.token.scopes))

    return check


def run_auth_checks(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> bool:
    """Run auth checks with AND logic.

    All checks must pass for authorization to succeed.

    Auth checks can:
    - Return True to allow access
    - Return False to deny access
    - Raise AuthorizationError to deny with a custom message (propagates)
    - Raise other exceptions (masked for security, treated as denial)

    Args:
        checks: A single check function or list of check functions.
        ctx: The auth context to pass to each check.

    Returns:
        True if all checks pass, False if any check fails.

    Raises:
        AuthorizationError: If an auth check explicitly raises it.
    """
    check_list = [checks] if not isinstance(checks, list) else checks
    check_list = cast(list[AuthCheck], check_list)

    for check in check_list:
        try:
            if not check(ctx):
                return False
        except AuthorizationError:
            # Let AuthorizationError propagate with its custom message
            raise
        except Exception:
            # Mask other exceptions for security - log and treat as auth failure
            logger.warning(
                f"Auth check {getattr(check, '__name__', repr(check))} "
                "raised an unexpected exception",
                exc_info=True,
            )
            return False

    return True
