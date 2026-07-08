"""Server-level cache hints for FastMCP (SEP-2549).

A FastMCP server opts every SDK-cacheable result it emits into client-side
caching by setting `cache_ttl` (seconds) and, optionally, `cache_scope` on the
`FastMCP` constructor. The hint is uniform by construction: one server-level
value applies to `tools/list`, `prompts/list`, `resources/list`,
`resources/templates/list`, `resources/read`, and `server/discover` alike — no
per-component surface and no aggregation.

FastMCP does not hand-set the wire fields. It passes the hint through to the SDK
low-level `Server(cache_hints=...)`, whose runner fills `ttlMs`/`cacheScope` on
every cacheable result via `apply_cache_hint`, leaving any field a handler set
explicitly untouched. Honoring is modern-only and opt-in on the client: a hinted
server is inert unless the client passes `cache=` and negotiates `2026-07-28`.
"""

from __future__ import annotations

from typing import Literal, get_args

from mcp.server.caching import CacheHint
from mcp_types.methods import CacheableMethod

CacheScope = Literal["public", "private"]
"""Whether a cached result may be shared across authorization contexts
(`"public"`) or reused only within the one that produced it (`"private"`)."""


def build_cache_hints(
    cache_ttl: int | None,
    cache_scope: CacheScope | None,
) -> dict[CacheableMethod, CacheHint] | None:
    """Build the per-method `CacheHint` map for the SDK low-level server.

    `cache_ttl` is in seconds and is converted to the wire's milliseconds. When
    `cache_ttl` is `None` the server emits no hint, so its wire output is
    identical to a server that never set one; a `cache_scope` given without a
    `cache_ttl` is meaningless (the client gates caching on the presence of a
    TTL) and is rejected rather than silently ignored.

    Returns `None` when no hint is set, or a map applying the same hint to every
    SDK-cacheable method otherwise.

    Raises:
        ValueError: If `cache_ttl` is not positive, or if `cache_scope` is set
            without `cache_ttl`.
    """
    if cache_ttl is None:
        if cache_scope is not None:
            raise ValueError(
                "cache_scope requires cache_ttl; a scope without a TTL does not "
                "enable caching"
            )
        return None
    if cache_ttl <= 0:
        raise ValueError(f"cache_ttl must be a positive integer, got {cache_ttl}")
    hint = CacheHint(ttl_ms=cache_ttl * 1000, scope=cache_scope or "private")
    return dict.fromkeys(get_args(CacheableMethod), hint)
