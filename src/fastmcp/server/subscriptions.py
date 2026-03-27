"""Resource subscription registry for FastMCP.

Tracks which client sessions have subscribed to which resource URIs,
enabling the server to push ``notifications/resources/updated`` when
a resource changes.

Design notes
------------
- Sessions are stored as ``weakref`` objects so that a disconnected
  session is automatically removed without explicit cleanup.
- A per-URI lock is used so that concurrent subscribe/unsubscribe
  calls on the same URI are race-free without blocking unrelated URIs.
- The registry is a module-level singleton; import :func:`get_registry`
  to access it.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.session import ServerSession


class ResourceSubscriptionRegistry:
    """In-memory per-URI registry of subscribed sessions.

    Sessions are stored as weak references so that garbage-collected
    (disconnected) sessions are automatically evicted.
    """

    def __init__(self) -> None:
        # uri -> set of weak refs to ServerSession
        self._subscriptions: dict[str, set[weakref.ref[ServerSession]]] = defaultdict(
            set
        )
        self._lock = asyncio.Lock()

    async def subscribe(self, uri: str, session: ServerSession) -> None:
        """Record that *session* wants to receive updates for *uri*."""
        async with self._lock:
            self._subscriptions[uri].add(weakref.ref(session))

    async def unsubscribe(self, uri: str, session: ServerSession) -> None:
        """Remove *session*'s subscription for *uri* (no-op if not subscribed)."""
        async with self._lock:
            subscribers = self._subscriptions.get(uri)
            if subscribers is None:
                return
            # Remove any ref that points to this session (or is dead)
            self._subscriptions[uri] = {
                ref for ref in subscribers if ref() not in (None, session)
            }
            if not self._subscriptions[uri]:
                del self._subscriptions[uri]

    def get_subscribers(self, uri: str) -> list[ServerSession]:
        """Return live sessions subscribed to *uri*.

        Dead weak references are silently skipped.
        """
        refs = self._subscriptions.get(uri, set())
        live: list[ServerSession] = []
        for ref in list(refs):
            session = ref()
            if session is not None:
                live.append(session)
        return live

    async def remove_session(self, session: ServerSession) -> None:
        """Remove all subscriptions for *session* (called on disconnect)."""
        async with self._lock:
            to_delete: list[str] = []
            for uri, refs in self._subscriptions.items():
                self._subscriptions[uri] = {
                    ref for ref in refs if ref() not in (None, session)
                }
                if not self._subscriptions[uri]:
                    to_delete.append(uri)
            for uri in to_delete:
                del self._subscriptions[uri]


_registry: ResourceSubscriptionRegistry | None = None


def get_registry() -> ResourceSubscriptionRegistry:
    """Return the module-level singleton registry, creating it if needed."""
    global _registry
    if _registry is None:
        _registry = ResourceSubscriptionRegistry()
    return _registry
