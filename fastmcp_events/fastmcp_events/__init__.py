"""Protocol support for the MCP Events extension (SEP-3415).

Experimental: this API may change in any release without a deprecation period.
"""

from typing import TYPE_CHECKING

from fastmcp_events.models import (
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
    "Event": "fastmcp_events.extension",
    "EventBacklogFullError": "fastmcp_events.subscription",
    "EventBatch": "fastmcp_events.extension",
    "EventPoll": "fastmcp_events.extension",
    "EventsExtension": "fastmcp_events.extension",
    "EventsClientExtension": "fastmcp_events.client",
    "EventSubscription": "fastmcp_events.subscription",
    "list_events": "fastmcp_events.subscription",
    "list_events_mcp": "fastmcp_events.subscription",
    "poll_events_mcp": "fastmcp_events.subscription",
    "subscribe_events": "fastmcp_events.subscription",
}

if TYPE_CHECKING:
    from fastmcp_events.subscription import (
        EventBacklogFullError as EventBacklogFullError,
    )
    from fastmcp_events.subscription import (
        EventSubscription as EventSubscription,
    )
    from fastmcp_events.subscription import list_events as list_events
    from fastmcp_events.subscription import (
        list_events_mcp as list_events_mcp,
    )
    from fastmcp_events.subscription import (
        poll_events_mcp as poll_events_mcp,
    )
    from fastmcp_events.subscription import (
        subscribe_events as subscribe_events,
    )
    from fastmcp_events.client import (
        EventsClientExtension as EventsClientExtension,
    )
    from fastmcp_events.extension import Event as Event
    from fastmcp_events.extension import EventBatch as EventBatch
    from fastmcp_events.extension import EventPoll as EventPoll
    from fastmcp_events.extension import EventsExtension as EventsExtension


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)
