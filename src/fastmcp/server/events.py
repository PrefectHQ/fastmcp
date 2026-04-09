"""Subscription infrastructure for MCP events.

This module provides:
- SubscriptionRegistry for managing session-to-topic subscriptions with MQTT wildcards
- RetainedValueStore for storing the most recent event per topic

Event types (EventEffect, EventTopicDescriptor, EventParams, etc.) are imported
from the mcp SDK (mcp.types). This module re-exports them for convenience.

NOTE: This is completely separate from ``event_store.py`` which handles
SSE transport-level resumability for Streamable HTTP.

Wildcard rules:
- ``+`` matches exactly one segment (between ``/`` separators)
- ``#`` matches zero or more trailing segments (must be last segment)
- Literal segments match exactly
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

# Re-export event types from the SDK for convenience
from mcp.types import (  # noqa: F401
    EventEffect,
    EventEmitNotification,
    EventListRequest,
    EventListResult,
    EventParams,
    EventsCapability,
    EventSubscribeParams,
    EventSubscribeRequest,
    EventSubscribeResult,
    EventTopicDescriptor,
    EventUnsubscribeParams,
    EventUnsubscribeRequest,
    EventUnsubscribeResult,
    RejectedTopic,
    RetainedEvent,
    SubscribedTopic,
)

# ---------------------------------------------------------------------------
# Wildcard pattern matching
# ---------------------------------------------------------------------------


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert an MQTT-style topic pattern to a compiled regex.

    ``+`` becomes a single-segment match, ``#`` becomes a greedy
    multi-segment match (only valid as the final segment).
    """
    parts = pattern.split("/")
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if part == "#":
            if i != len(parts) - 1:
                raise ValueError("'#' wildcard is only valid as the last segment")
            # # matches zero or more trailing segments
            # If preceding segments exist, the / before # is optional
            # so "myapp/#" matches both "myapp" and "myapp/anything"
            if regex_parts:
                return re.compile("^" + "/".join(regex_parts) + "(/.*)?$")
            else:
                return re.compile("^.*$")
        elif part == "+":
            regex_parts.append("[^/]+")
        else:
            regex_parts.append(re.escape(part))
    return re.compile("^" + "/".join(regex_parts) + "$")


# ---------------------------------------------------------------------------
# Subscription registry
# ---------------------------------------------------------------------------


class SubscriptionRegistry:
    """Thread-safe registry mapping session IDs to topic subscription patterns.

    Supports MQTT-style wildcards (``+`` for single segment, ``#`` for
    trailing multi-segment).  ``match()`` guarantees at-most-once delivery
    per session regardless of how many patterns overlap.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscriptions: dict[str, set[str]] = {}
        self._compiled: dict[str, re.Pattern[str]] = {}

    def _compile(self, pattern: str) -> re.Pattern[str]:
        if pattern not in self._compiled:
            self._compiled[pattern] = _pattern_to_regex(pattern)
        return self._compiled[pattern]

    async def add(self, session_id: str, pattern: str) -> None:
        """Register a subscription for *session_id* on *pattern*."""
        async with self._lock:
            self._subscriptions.setdefault(session_id, set()).add(pattern)
            self._compile(pattern)

    async def remove(self, session_id: str, pattern: str) -> None:
        """Remove a single subscription."""
        async with self._lock:
            if session_id in self._subscriptions:
                self._subscriptions[session_id].discard(pattern)
                if not self._subscriptions[session_id]:
                    del self._subscriptions[session_id]

    async def remove_all(self, session_id: str) -> None:
        """Remove all subscriptions for *session_id* (disconnect cleanup)."""
        async with self._lock:
            self._subscriptions.pop(session_id, None)

    async def match(self, topic: str) -> set[str]:
        """Return session IDs whose subscriptions match *topic*.

        Each session appears at most once (at-most-once delivery guarantee).
        """
        async with self._lock:
            result: set[str] = set()
            for session_id, patterns in self._subscriptions.items():
                for pattern in patterns:
                    regex = self._compile(pattern)
                    if regex.match(topic):
                        result.add(session_id)
                        break  # at-most-once per session
            return result

    async def get_subscriptions(self, session_id: str) -> set[str]:
        """Return the set of patterns a session is subscribed to."""
        async with self._lock:
            return set(self._subscriptions.get(session_id, set()))


# ---------------------------------------------------------------------------
# Retained value store
# ---------------------------------------------------------------------------


class RetainedValueStore:
    """Stores the most recent event per topic for replay on subscribe.

    This is an *application-level* retained value store, distinct from
    ``event_store.py`` which is an SSE transport-level event store for
    Streamable HTTP resumability.

    All mutating and reading methods are async and guarded by an
    ``asyncio.Lock`` to prevent races between concurrent emit and
    subscribe operations (mirrors ``SubscriptionRegistry``'s pattern).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._store: dict[str, RetainedEvent] = {}
        self._expires: dict[str, str] = {}
        self._regex_cache: dict[str, re.Pattern[str]] = {}

    async def set(
        self, topic: str, event: RetainedEvent, expires_at: str | None = None
    ) -> None:
        """Store or replace the retained value for *topic*."""
        async with self._lock:
            self._store[topic] = event
            if expires_at is not None:
                self._expires[topic] = expires_at
            else:
                self._expires.pop(topic, None)

    async def get(self, topic: str) -> RetainedEvent | None:
        """Retrieve the retained value, or ``None`` if expired/absent."""
        async with self._lock:
            event = self._store.get(topic)
            if event is None:
                return None
            if self._is_expired(topic):
                del self._store[topic]
                self._expires.pop(topic, None)
                return None
            return event

    async def get_matching(self, pattern: str) -> list[RetainedEvent]:
        """Return all non-expired retained events whose topic matches *pattern*."""
        async with self._lock:
            if pattern not in self._regex_cache:
                self._regex_cache[pattern] = _pattern_to_regex(pattern)
            regex = self._regex_cache[pattern]
            result: list[RetainedEvent] = []
            expired_topics: list[str] = []
            for topic, event in self._store.items():
                if self._is_expired(topic):
                    expired_topics.append(topic)
                    continue
                if regex.match(topic):
                    # Each topic has exactly one retained event in the store,
                    # so no per-topic deduplication is needed here. The caller
                    # is responsible for deduplicating across multiple pattern
                    # matches (e.g. when processing a subscribe request with
                    # overlapping patterns).
                    result.append(event)
            for topic in expired_topics:
                del self._store[topic]
                self._expires.pop(topic, None)
            return result

    async def delete(self, topic: str) -> None:
        """Remove the retained value for *topic*."""
        async with self._lock:
            self._store.pop(topic, None)
            self._expires.pop(topic, None)

    def _is_expired(self, topic: str) -> bool:
        expires_at = self._expires.get(topic)
        if expires_at is None:
            return False
        try:
            # Python 3.10 fromisoformat() doesn't support "Z" suffix;
            # replace with "+00:00" for compatibility.
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= expiry
        except (ValueError, TypeError):
            return False
