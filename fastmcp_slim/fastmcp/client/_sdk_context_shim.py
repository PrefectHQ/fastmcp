"""SDK context survival shims pending the Phase C client port.

The MCP SDK v2 removed ``mcp.shared.context.RequestContext`` and
``mcp.shared.context.LifespanContextT``. Client-side handler signatures in
FastMCP still reference them as type annotations. These shims keep those
modules importable at collection time; the annotations they feed are not
semantically load-bearing yet.

TODO(sdkv2): replace with the real SDK client request-context type in Phase C
(client port). Handler wiring that populates/consumes the request context is
reworked there.
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
