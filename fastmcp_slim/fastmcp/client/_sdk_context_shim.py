"""Subscriptable request-context alias for FastMCP client handler signatures.

FastMCP exposes public generic handler type aliases (``SamplingHandler``,
``RootsHandler``, ``ElicitationHandler``) parameterized over a session and a
lifespan-context type. The MCP SDK v2 request context (
``mcp.client.ClientRequestContext``) is a plain ``kw_only`` dataclass and is
NOT subscriptable, so it cannot back those two-parameter aliases directly.

This module keeps a subscriptable ``RequestContext[SessionT, LifespanContextT]``
generic so the public alias surface is preserved unchanged. It is a permanent
part of the client type surface, not a migration placeholder. The concrete
context object handlers receive at runtime is the SDK's ``ClientRequestContext``;
our ``create_*_callback`` wrappers pass it through opaquely.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

LifespanContextT = TypeVar("LifespanContextT")
_SessionT = TypeVar("_SessionT")


class RequestContext(Generic[_SessionT, LifespanContextT]):
    """Placeholder for the removed SDK ``RequestContext`` generic.

    Subscriptable with two type parameters to match existing client handler
    annotations. Not instantiated anywhere; exists only so module imports and
    annotation evaluation succeed until the Phase C client port lands.
    """

    def __class_getitem__(cls, item: Any) -> Any:  # pragma: no cover - typing only
        return super().__class_getitem__(item)  # type: ignore[misc]
