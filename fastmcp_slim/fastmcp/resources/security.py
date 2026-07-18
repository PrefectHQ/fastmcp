"""Path-safety policy for templated resource parameters.

Templated resources (`@mcp.resource("file:///{path}")`-style) extract
parameter values straight out of the request URI and hand them to the
resource function. When those values flow into filesystem or URI
construction, a malicious client can smuggle path-traversal payloads
(`../`, absolute paths, null bytes) through the template.

`ResourceSecurity` screens extracted parameter values *before* the
resource handler runs. It is applied by default to every templated
read, mirroring the posture of the underlying MCP SDK's
`ResourceSecurity` (traversal, absolute paths, and null bytes rejected).

The screening reuses the SDK's component-based traversal check, so a
value that merely *contains* dots (e.g. `HEAD~3..HEAD`, `v1..v2`,
`file.tar.gz`) is not rejected — only an actual `..` path segment is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

__all__ = ["ResourceSecurity"]


@cache
def _path_checks() -> tuple[Callable[[str], bool], Callable[[str], bool]]:
    """Lazily load the SDK's path-safety helpers.

    The screening logic lives in the `mcp` SDK, which is an optional
    dependency of `fastmcp-slim`. Importing it at module top would make
    `from fastmcp.resources import Resource` require the SDK, so the
    import is deferred to the point of first use (and cached).
    """
    from mcp.shared.path_security import (
        contains_path_traversal,
        is_absolute_path,
    )

    return contains_path_traversal, is_absolute_path


class InheritSecurity:
    """Sentinel type: inherit the server-wide resource-security default.

    Distinguishes "no per-component policy was set" (inherit whatever the
    server configured) from an explicit ``None`` (screening disabled for
    this component).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "INHERIT_SECURITY"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        # Accept the singleton sentinel as-is; it is an internal, excluded
        # field value, so no serialization support is needed.
        return core_schema.is_instance_schema(cls)


INHERIT_SECURITY = InheritSecurity()
"""Sentinel instance signalling a template should inherit the server default."""


@dataclass(frozen=True)
class ResourceSecurity:
    """Security policy applied to extracted resource template parameters.

    These checks run after a URI has matched a template and its
    parameter values have been extracted and percent-decoded. They catch
    path-traversal and absolute-path injection regardless of how the
    value was encoded in the URI (literal, `%2F`, `%5C`, `%2E%2E`).

    All checks default on. Screen a value like `HEAD~3..HEAD` (dots
    inside a single segment) passes — only a standalone `..` segment is
    treated as traversal.

    Example:
        Opt a parameter out of screening (e.g. a git ref that may
        legitimately contain `..`):

        ```python
        from fastmcp.resources import ResourceSecurity

        @mcp.resource(
            "git://diff/{ref}",
            security=ResourceSecurity(exempt_params={"ref"}),
        )
        def git_diff(ref: str) -> str: ...
        ```
    """

    reject_path_traversal: bool = True
    """Reject values containing `..` as a path component."""

    reject_absolute_paths: bool = True
    """Reject values that look like absolute filesystem paths."""

    reject_null_bytes: bool = True
    """Reject values containing NUL (`\\x00`). Null bytes defeat string
    comparisons (`"..\\x00" != ".."`) and can cause truncation in C
    extensions or subprocess calls."""

    exempt_params: Set[str] = field(default_factory=frozenset)
    """Parameter names to skip all checks for. Hyphenated URI-template
    spellings are accepted: `{git-ref}` is extracted as `git_ref`, and an
    exemption written either way matches it."""

    def _exempt(self, name: str) -> bool:
        """True if `name` is exempted under either its extracted or its
        URI-template spelling (hyphens normalize to underscores on
        extraction, so `exempt_params={"git-ref"}` must match `git_ref`)."""
        if name in self.exempt_params:
            return True
        return any(exempt.replace("-", "_") == name for exempt in self.exempt_params)

    def validate(self, params: Mapping[str, object]) -> str | None:
        """Check all parameter values against the configured policy.

        String values (and lists of strings, from wildcard `{path*}`
        parameters that span multiple segments) are screened; non-string
        values are ignored, since traversal is a string-path concern.

        Args:
            params: Extracted template parameters.

        Returns:
            The name of the first parameter that fails, or `None` if all
            values pass.
        """
        contains_path_traversal, is_absolute_path = _path_checks()
        for name, value in params.items():
            if self._exempt(name):
                continue
            if isinstance(value, str):
                candidates = [value]
            elif isinstance(value, (list, tuple)):
                candidates = [v for v in value if isinstance(v, str)]
            else:
                continue
            for candidate in candidates:
                if self.reject_null_bytes and "\0" in candidate:
                    return name
                if self.reject_path_traversal and contains_path_traversal(candidate):
                    return name
                if self.reject_absolute_paths and is_absolute_path(candidate):
                    return name
        return None


DEFAULT_RESOURCE_SECURITY = ResourceSecurity()
"""Secure-by-default policy: traversal, absolute paths, and null bytes rejected."""
