"""In-memory cache for token verification results.

Provides a generic TTL-based cache for ``AccessToken`` objects, designed to
reduce repeated network calls during opaque-token verification.  Only
*successful* verifications should be cached; errors and failures must be
retried on every request.

Example:
    ```python
    from fastmcp.utilities.token_cache import TokenCache

    cache = TokenCache(ttl_seconds=300, max_size=10000)

    # On cache miss, call the upstream verifier and store the result.
    hit, token = cache.get(raw_token)
    if not hit:
        token = await _call_upstream(raw_token)
        if token is not None:
            cache.set(raw_token, token)
    ```
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_CACHE_SIZE = 10_000
_CLEANUP_INTERVAL = 60  # seconds between periodic sweeps


@dataclass
class _CacheEntry:
    """A cached token result with its absolute expiration timestamp."""

    result: AccessToken
    expires_at: float


class TokenCache:
    """TTL-based in-memory cache for ``AccessToken`` objects.

    Features:
    - SHA-256 hashed cache keys (fixed size, regardless of token length).
    - Per-entry TTL that respects both the configured ``ttl_seconds`` and the
      token's own ``expires_at`` claim (whichever is sooner).
    - Bounded size with FIFO eviction when the cache is full.
    - Periodic cleanup of expired entries to prevent unbounded growth.
    - Defensive deep copies on both store and retrieve to prevent
      callers from mutating cached values.

    Caching is disabled when ``ttl_seconds`` is ``None``, ``0``, or
    negative, or when ``max_size`` is ``0`` or negative.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int | None = None,
        max_size: int | None = None,
    ) -> None:
        """Initialise the cache.

        Args:
            ttl_seconds: How long cached entries remain valid, in seconds.
                ``None`` or ``0`` disables caching entirely.
            max_size: Upper bound on the number of entries.  When the limit is
                reached, expired entries are swept first; if still full the
                oldest entry is evicted.  Defaults to 10 000.
        """
        self._ttl = ttl_seconds or 0
        self._max_size = max_size if max_size is not None else DEFAULT_MAX_CACHE_SIZE
        self._entries: dict[str, _CacheEntry] = {}
        self._last_cleanup = time.monotonic()

    @property
    def enabled(self) -> bool:
        """Return whether caching is active."""
        return self._ttl > 0 and self._max_size > 0

    # -- public API ----------------------------------------------------------

    def get(self, token: str) -> tuple[bool, AccessToken | None]:
        """Look up a cached verification result.

        Returns:
            ``(True, AccessToken)`` on a cache hit, ``(False, None)`` on a miss
            or when caching is disabled.  The returned ``AccessToken`` is a deep
            copy that is safe to mutate.
        """
        if not self.enabled:
            return (False, None)

        cache_key = self._hash_token(token)
        entry = self._entries.get(cache_key)

        if entry is None:
            return (False, None)

        if entry.expires_at < time.time():
            del self._entries[cache_key]
            return (False, None)

        return (True, entry.result.model_copy(deep=True))

    def set(self, token: str, result: AccessToken) -> None:
        """Store a *successful* verification result.

        Only successful verifications should be cached.  Failures (inactive
        tokens, missing scopes, HTTP errors, timeouts) must **not** be cached
        so that transient problems do not produce sticky false negatives.
        """
        if not self.enabled:
            return

        self._maybe_cleanup()
        self._enforce_size_limit()

        cache_key = self._hash_token(token)

        expires_at = time.time() + self._ttl
        if result.expires_at:
            expires_at = min(expires_at, float(result.expires_at))

        self._entries[cache_key] = _CacheEntry(
            result=result.model_copy(deep=True),
            expires_at=expires_at,
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _hash_token(token: str) -> str:
        """Return the SHA-256 hex digest of *token*."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _cleanup_expired(self) -> None:
        """Remove all entries whose TTL has elapsed."""
        now = time.time()
        expired = [k for k, v in self._entries.items() if v.expires_at < now]
        for key in expired:
            del self._entries[key]
        if expired:
            logger.debug("Cleaned up %d expired cache entries", len(expired))

    def _maybe_cleanup(self) -> None:
        """Run ``_cleanup_expired`` at most once per cleanup interval."""
        now = time.monotonic()
        if now - self._last_cleanup > _CLEANUP_INTERVAL:
            self._cleanup_expired()
            self._last_cleanup = now

    def _enforce_size_limit(self) -> None:
        """Ensure there is room for at least one new entry."""
        if len(self._entries) < self._max_size:
            return
        self._cleanup_expired()
        if len(self._entries) >= self._max_size:
            oldest_key = next(iter(self._entries))
            del self._entries[oldest_key]
