"""Authorization checks for FastMCP components.

Auth checks are callables that receive an ``AuthContext`` and return True to
allow access or False to deny it. They can also raise ``AuthorizationError`` to
deny with a custom message; other exceptions are masked and treated as denial.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastmcp.exceptions import AuthorizationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastmcp.server.auth import AccessToken
    from fastmcp.tools.base import Tool
    from fastmcp.utilities.components import FastMCPComponent


@dataclass
class AuthContext:
    """Context passed to auth check callables.

    Attributes:
        token: The current access token, or None if unauthenticated.
        component: The tool, resource, resource template, or prompt being accessed.
        tool: Backwards-compatible alias for component when it is a Tool.
    """

    token: AccessToken | None
    component: FastMCPComponent

    @property
    def tool(self) -> Tool | None:
        """Backwards-compatible access to the component as a Tool."""
        from fastmcp.tools.base import Tool

        return self.component if isinstance(self.component, Tool) else None


AuthCheck = Callable[[AuthContext], bool] | Callable[[AuthContext], Awaitable[bool]]


class _ScopeAwareCheck:
    """Base for auth checks that can name the scopes a token is missing.

    Ordinary auth checks are opaque booleans: on denial they reveal nothing
    about *why*. Scope-based checks expose their unmet requirements through
    `missing_scopes` so a shortfall can be surfaced as a spec-correct
    ``insufficient_scope`` step-up (SEP-2350 / RFC 6750 §3) naming exactly what
    the caller must re-authorize for.
    """

    def missing_scopes(self, ctx: AuthContext) -> set[str]:
        """Return the required scopes the token lacks (empty if satisfied).

        An absent token yields an empty set: a missing token is an
        authentication problem, not a scope shortfall, and must not be turned
        into an ``insufficient_scope`` challenge (RFC 6750 §3.1).
        """
        raise NotImplementedError


class _RequireScopes(_ScopeAwareCheck):
    """Callable auth check requiring all of a fixed set of OAuth scopes."""

    def __init__(self, scopes: tuple[str, ...]) -> None:
        self.required_scopes: frozenset[str] = frozenset(scopes)

    def __call__(self, ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        return self.required_scopes.issubset(set(ctx.token.scopes))

    def missing_scopes(self, ctx: AuthContext) -> set[str]:
        if ctx.token is None:
            return set()
        return set(self.required_scopes) - set(ctx.token.scopes)


class _RestrictTag(_ScopeAwareCheck):
    """Callable auth check requiring scopes only when a component has a tag."""

    def __init__(self, tag: str, scopes: list[str]) -> None:
        self.tag = tag
        self.required_scopes: frozenset[str] = frozenset(scopes)

    def __call__(self, ctx: AuthContext) -> bool:
        if self.tag not in ctx.component.tags:
            return True
        if ctx.token is None:
            return False
        return self.required_scopes.issubset(set(ctx.token.scopes))

    def missing_scopes(self, ctx: AuthContext) -> set[str]:
        if self.tag not in ctx.component.tags or ctx.token is None:
            return set()
        return set(self.required_scopes) - set(ctx.token.scopes)


def require_scopes(*scopes: str) -> AuthCheck:
    """Require all of the given OAuth scopes."""
    return _RequireScopes(scopes)


def restrict_tag(tag: str, *, scopes: list[str]) -> AuthCheck:
    """Require scopes when the accessed component has a specific tag."""
    return _RestrictTag(tag, scopes)


def unmet_scopes(checks: AuthCheck | list[AuthCheck], ctx: AuthContext) -> list[str]:
    """Collect the scopes required by scope-based checks that the token lacks.

    Only scope-aware checks (`require_scopes`, `restrict_tag`) expose their
    requirements; other checks are opaque and contribute nothing. The result is
    sorted and de-duplicated so a shortfall can be named in an
    ``insufficient_scope`` step-up signal. An empty list means either the checks
    are satisfied or the denial was not scope-based (e.g. a missing token).
    """
    check_list = checks if isinstance(checks, list) else [checks]
    missing: set[str] = set()
    for check in check_list:
        if isinstance(check, _ScopeAwareCheck):
            missing |= check.missing_scopes(ctx)
    return sorted(missing)


async def run_auth_checks(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> bool:
    """Run auth checks with AND logic."""
    check_list = [checks] if not isinstance(checks, list) else checks
    check_list = cast(list[AuthCheck], check_list)

    for check in check_list:
        try:
            result = check(ctx)
            if inspect.isawaitable(result):
                result = await result
            if not result:
                return False
        except AuthorizationError:
            raise
        except Exception:
            logger.warning(
                f"Auth check {getattr(check, '__name__', repr(check))} "
                "raised an unexpected exception",
                exc_info=True,
            )
            return False

    return True
