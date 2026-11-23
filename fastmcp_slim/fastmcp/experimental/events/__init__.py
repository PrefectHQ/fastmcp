"""Protocol support for the MCP Events extension (SEP-3415).

Experimental: this API may change in any release without a deprecation period.
"""

from typing import TYPE_CHECKING

from fastmcp.experimental.events.models import (
    EventOccurrence,
    EventType,
    ListEventsResult,
    PollEventsResult,
)

__all__ = [
    "Event",
    "EventBacklogFullError",
    "EventBatch",
    "EventOccurrence",
    "EventPayload",
    "EventPoll",
    "EventSubscription",
    "EventType",
    "EventsClientExtension",
    "EventsExtension",
    "ListEventsResult",
    "PollEventsResult",
    "list_events",
    "list_events_mcp",
    "poll_events_mcp",
    "subscribe_events",
]

_LAZY = {
    "Event": "fastmcp.experimental.events.extension",
    "EventBacklogFullError": "fastmcp.experimental.events.subscription",
    "EventBatch": "fastmcp.experimental.events.extension",
    "EventPayload": "fastmcp.experimental.events.payload",
    "EventPoll": "fastmcp.experimental.events.extension",
    "EventsExtension": "fastmcp.experimental.events.extension",
    "EventsClientExtension": "fastmcp.experimental.events.client",
    "EventSubscription": "fastmcp.experimental.events.subscription",
    "list_events": "fastmcp.experimental.events.subscription",
    "list_events_mcp": "fastmcp.experimental.events.subscription",
    "poll_events_mcp": "fastmcp.experimental.events.subscription",
    "subscribe_events": "fastmcp.experimental.events.subscription",
}

if TYPE_CHECKING:
    from fastmcp.experimental.events.subscription import (
        EventBacklogFullError as EventBacklogFullError,
    )
    from fastmcp.experimental.events.subscription import (
        EventSubscription as EventSubscription,
    )
    from fastmcp.experimental.events.subscription import list_events as list_events
    from fastmcp.experimental.events.subscription import (
        list_events_mcp as list_events_mcp,
    )
    from fastmcp.experimental.events.subscription import (
        poll_events_mcp as poll_events_mcp,
    )
    from fastmcp.experimental.events.subscription import (
        subscribe_events as subscribe_events,
    )
    from fastmcp.experimental.events.client import (
        EventsClientExtension as EventsClientExtension,
    )
    from fastmcp.experimental.events.extension import Event as Event
    from fastmcp.experimental.events.extension import EventBatch as EventBatch
    from fastmcp.experimental.events.extension import EventPoll as EventPoll
    from fastmcp.experimental.events.payload import EventPayload as EventPayload
    from fastmcp.experimental.events.extension import EventsExtension as EventsExtension


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)
