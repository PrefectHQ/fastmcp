"""Central event bus for SecureMCP security alerts.

The ``SecurityEventBus`` is the core pub/sub hub. Components emit events
via ``emit()``, and subscribers receive events that match their filters.

Example::

    bus = SecurityEventBus()

    # Subscribe with a filter
    bus.subscribe(
        handler=my_handler,
        event_filter=SeverityFilter(min_severity=AlertSeverity.WARNING),
    )

    # Components emit events
    bus.emit(SecurityEvent(
        event_type=SecurityEventType.DRIFT_DETECTED,
        severity=AlertSeverity.WARNING,
        layer="reflexive",
        message="Drift detected",
    ))
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp.server.security.alerts.filters import EventFilter
from fastmcp.server.security.alerts.models import SecurityEvent

logger = logging.getLogger(__name__)


@dataclass
class Subscription:
    """A registered event subscription.

    Attributes:
        subscription_id: Unique identifier for this subscription.
        handler: Callable invoked when a matching event is emitted.
        event_filter: Optional filter to limit which events reach the handler.
        name: Human-readable name for debugging.
    """

    subscription_id: str
    handler: Callable[[SecurityEvent], Any]
    event_filter: EventFilter | None = None
    name: str = ""


class SecurityEventBus:
    """Central pub/sub event bus for security alerts.

    All SecureMCP components can emit events to the bus, and external
    systems can subscribe to receive filtered events in real-time.

    The bus is **synchronous** — ``emit()`` calls handlers inline.
    Handlers should be fast; long-running work should be offloaded.

    Example::

        bus = SecurityEventBus()
        events = []
        bus.subscribe(handler=events.append, name="collector")
        bus.emit(some_event)
        assert len(events) == 1
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._event_count: int = 0
        self._error_count: int = 0

    def subscribe(
        self,
        handler: Callable[[SecurityEvent], Any],
        event_filter: EventFilter | None = None,
        name: str = "",
    ) -> str:
        """Register a handler to receive events.

        Args:
            handler: Callable invoked with each matching ``SecurityEvent``.
            event_filter: Optional filter to limit events. If None, all events
                are delivered.
            name: Human-readable name for this subscription.

        Returns:
            Subscription ID (use with ``unsubscribe()``).
        """
        sub_id = str(uuid.uuid4())
        self._subscriptions[sub_id] = Subscription(
            subscription_id=sub_id,
            handler=handler,
            event_filter=event_filter,
            name=name or f"sub-{sub_id[:8]}",
        )
        logger.debug("Subscription added: %s (%s)", name or sub_id[:8], sub_id)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription.

        Args:
            subscription_id: The ID returned by ``subscribe()``.

        Returns:
            True if the subscription was found and removed, False otherwise.
        """
        sub = self._subscriptions.pop(subscription_id, None)
        if sub is not None:
            logger.debug("Subscription removed: %s", sub.name)
            return True
        return False

    def emit(self, event: SecurityEvent) -> int:
        """Emit an event to all matching subscribers.

        Args:
            event: The security event to broadcast.

        Returns:
            Number of handlers that received the event.
        """
        self._event_count += 1
        delivered = 0

        for sub in list(self._subscriptions.values()):
            try:
                if sub.event_filter is not None and not sub.event_filter.matches(event):
                    continue
                sub.handler(event)
                delivered += 1
            except Exception as exc:
                self._error_count += 1
                logger.error(
                    "Handler '%s' failed on event %s: %s",
                    sub.name,
                    event.event_type.value,
                    exc,
                )

        return delivered

    @property
    def subscription_count(self) -> int:
        """Number of active subscriptions."""
        return len(self._subscriptions)

    @property
    def event_count(self) -> int:
        """Total number of events emitted."""
        return self._event_count

    @property
    def error_count(self) -> int:
        """Total number of handler errors."""
        return self._error_count

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._subscriptions.clear()
