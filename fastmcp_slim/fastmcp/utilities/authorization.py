"""Authorization checks for FastMCP components.

Auth checks are callables that receive an ``AuthContext`` and return True to
allow access or False to deny it. They can also raise ``AuthorizationError`` to
deny with a custom message; other exceptions are masked and treated as denial.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

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


class _RequireRoles:
    """Callable auth check requiring all of a fixed set of roles.

    Deliberately not a :class:`_ScopeAwareCheck`. Roles are not scopes and
    cannot be requested through OAuth, so a shortfall has no spec-correct
    step-up representation.
    """

    def __init__(
        self,
        roles: tuple[str, ...],
        extract: Callable[[dict[str, Any]], Iterable[str]],
    ) -> None:
        self.required_roles: frozenset[str] = frozenset(roles)
        self._extract = extract

    def __call__(self, ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        try:
            extracted = self._extract(ctx.token.claims)
            # A provider that stores a single role as a bare string satisfies
            # `Iterable[str]`, but iterating it yields characters: "admin"
            # would deny an "admin" requirement and grant an "a" one. Treat a
            # string as the single role it plainly means.
            if isinstance(extracted, str):
                extracted = [extracted]
            granted = set(extracted)
        except (KeyError, IndexError, TypeError):
            # A caller whose token simply lacks the claim is an ordinary
            # denial, not a broken check: return False rather than letting
            # `_evaluate_check` log a warning on every unauthorized request.
            return False
        return self.required_roles.issubset(granted)


def require_scopes(*scopes: str) -> AuthCheck:
    """Require all of the given OAuth scopes."""
    return _RequireScopes(scopes)


def require_roles(
    *roles: str,
    extract: Callable[[dict[str, Any]], Iterable[str]],
) -> AuthCheck:
    """Require all of the given roles, read from the token's claims.

    Roles and groups are not part of OIDC, so every identity provider puts them
    somewhere different: `realm_access.roles` on Keycloak, `roles` on Microsoft
    Entra, `cognito:groups` on AWS Cognito, `permissions` or a namespaced custom
    claim on Auth0. `extract` receives the token's claims and returns the
    caller's roles, which keeps that provider-specific knowledge at the call
    site instead of guessing it here.

    ```python
    from fastmcp.server.auth import require_roles

    keycloak = require_roles("admin", extract=lambda c: c["realm_access"]["roles"])
    cognito = require_roles("admins", extract=lambda c: c["cognito:groups"])
    ```

    A token missing the claim entirely is denied rather than treated as an
    error, so `extract` may index into the claims without guarding. An
    extractor returning a bare string is treated as one role, since a provider
    that stores a single role as a scalar is common.

    Unlike `require_scopes`, this check cannot signal a shortfall: OAuth has no
    way to request a role, so there is no `insufficient_scope` challenge to
    emit. A role denial is therefore reported as a plain `AuthorizationError`,
    and it suppresses any scope shortfall alongside it — a caller blocked by
    their role must not be told to go obtain a scope that would not help.
    Scope shortfalls are still reported normally whenever the role check
    passes.

    Args:
        *roles: Roles the caller must hold. All are required (AND logic).
        extract: Callable mapping the token's claims to the caller's roles.

    Raises:
        ValueError: If no roles are given, which would allow any authenticated
            caller and is more likely a mistake than an intent.
    """
    if not roles:
        raise ValueError(
            "require_roles() needs at least one role; a check with no roles "
            "would admit any authenticated caller."
        )
    return _RequireRoles(roles, extract)


def restrict_tag(tag: str, *, scopes: list[str]) -> AuthCheck:
    """Require scopes when the accessed component has a specific tag."""
    return _RestrictTag(tag, scopes)


def scope_requirements(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> list[str] | None:
    """Scopes a check list requires but the token lacks, without running it.

    Returns ``None`` when the list contains any opaque (non-scope) check. Such a
    check might deny for a reason unrelated to scopes, and evaluating it here
    would run authorization logic — with whatever side effects it carries —
    outside its normal place in the chain. Since its verdict is unknown, its
    siblings' scopes must not be disclosed either, so the whole list is withheld.

    When every check is scope-aware, the result is their combined shortfall,
    computed purely from the token and component (an empty list means the list is
    already satisfied). This lets a shortfall be aggregated across authorization
    layers without evaluating anything that would otherwise be skipped.
    """
    check_list = [checks] if not isinstance(checks, list) else checks
    check_list = cast(list[AuthCheck], check_list)

    missing: set[str] = set()
    for check in check_list:
        if not isinstance(check, _ScopeAwareCheck):
            return None
        missing |= check.missing_scopes(ctx)
    return sorted(missing)


async def _evaluate_check(check: AuthCheck, ctx: AuthContext) -> bool:
    """Evaluate a single auth check, masking unexpected failures as denial.

    An ``AuthorizationError`` is the check's deliberate denial and propagates.
    Any other exception is a bug in the check; it is logged and treated as a
    denial so a broken check fails closed.
    """
    try:
        result = check(ctx)
        if inspect.isawaitable(result):
            result = await result
    except AuthorizationError:
        raise
    except Exception:
        logger.warning(
            f"Auth check {getattr(check, '__name__', repr(check))} "
            "raised an unexpected exception",
            exc_info=True,
        )
        return False
    return bool(result)


async def run_auth_checks_with_shortfall(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> tuple[bool, list[str]]:
    """Run auth checks with AND logic, classifying the denial cause.

    Returns ``(authorized, missing_scopes)``. ``missing_scopes`` names every
    scope the caller must obtain to satisfy *all* scope requirements at once:
    the union of the shortfalls across every scope-aware check, not just the
    first one to fail. Reporting only the first would strand a caller in a
    step-up loop — it obtains that scope, retries, and is denied again for the
    next — so the union is what makes a single re-authorization converge.

    The challenge is withheld entirely (an empty list, which the caller surfaces
    as a plain ``AuthorizationError``) unless every non-scope check passes. A
    custom policy denial — a tenant check, say — must never be reported as an
    ``insufficient_scope`` shortfall, and must never name the scopes of a
    component the caller could not otherwise reach. To guarantee that, the
    opaque checks are all evaluated before any scope is disclosed; a shortfall
    is only reported once they have all passed.

    An ``AuthorizationError`` raised by a check propagates unchanged.
    """
    check_list = [checks] if not isinstance(checks, list) else checks
    check_list = cast(list[AuthCheck], check_list)

    scope_shortfall = False
    for check in check_list:
        if await _evaluate_check(check, ctx):
            continue
        if not isinstance(check, _ScopeAwareCheck):
            # An opaque denial dominates: deny without disclosing any scope.
            return False, []
        # Keep going. The remaining opaque checks still have to pass before a
        # scope shortfall may be disclosed, and the remaining scope checks
        # contribute to the union.
        scope_shortfall = True

    if not scope_shortfall:
        return True, []

    # Every opaque check passed, so naming the shortfall is safe. `missing_scopes`
    # is a pure comparison against the token and returns an empty set for checks
    # that passed, so unioning across all of them yields exactly the unmet scopes.
    missing: set[str] = set()
    for check in check_list:
        if isinstance(check, _ScopeAwareCheck):
            missing |= check.missing_scopes(ctx)
    return False, sorted(missing)


async def run_auth_checks(
    checks: AuthCheck | list[AuthCheck],
    ctx: AuthContext,
) -> bool:
    """Run auth checks with AND logic, stopping at the first failure."""
    check_list = [checks] if not isinstance(checks, list) else checks
    check_list = cast(list[AuthCheck], check_list)

    for check in check_list:
        if not await _evaluate_check(check, ctx):
            return False

    return True
