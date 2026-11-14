"""Protocol support for the MCP Events extension (SEP-3415), poll delivery."""

from typing import TYPE_CHECKING

from fastmcp.events.models import (
    EventOccurrence,
    EventType,
    ListEventsResult,
    PollEventsResult,
)

__all__ = [
    "Event",
    "EventBatch",
    "EventOccurrence",
    "EventPoll",
    "EventType",
    "EventsClientExtension",
    "EventsExtension",
    "ListEventsResult",
    "PollEventsResult",
]

_LAZY = {
    "Event": "fastmcp.events.extension",
    "EventBatch": "fastmcp.events.extension",
    "EventPoll": "fastmcp.events.extension",
    "EventsExtension": "fastmcp.events.extension",
    "EventsClientExtension": "fastmcp.events.client",
}

if TYPE_CHECKING:
    from fastmcp.events.client import EventsClientExtension as EventsClientExtension
    from fastmcp.events.extension import Event as Event
    from fastmcp.events.extension import EventBatch as EventBatch
    from fastmcp.events.extension import EventPoll as EventPoll
    from fastmcp.events.extension import EventsExtension as EventsExtension


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)
