"""Authorization checks for FastMCP components."""

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
    """Context passed to auth check callables."""

    token: AccessToken | None
    component: FastMCPComponent

    @property
    def tool(self) -> Tool | None:
        from fastmcp.tools.base import Tool

        return self.component if isinstance(self.component, Tool) else None


AuthCheck = Callable[[AuthContext], bool] | Callable[[AuthContext], Awaitable[bool]]


def require_scopes(*scopes: str) -> AuthCheck:
    required = set(scopes)

    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        return required.issubset(set(ctx.token.scopes))

    return check


def restrict_tag(tag: str, *, scopes: list[str]) -> AuthCheck:
    required = set(scopes)

    def check(ctx: AuthContext) -> bool:
        if tag not in ctx.component.tags:
            return True
        if ctx.token is None:
            return False
        return required.issubset(set(ctx.token.scopes))

    return check


async def run_auth_checks(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> bool:
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
