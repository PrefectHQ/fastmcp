"""Lightweight session scoping for Streamable HTTP event stores."""

from __future__ import annotations

from mcp.server.streamable_http import (
    EventCallback,
    EventId,
    EventMessage,
    EventStore,
    StreamId,
)
from mcp_types import JSONRPCMessage

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class SessionScopedEventStore(EventStore):
    """EventStore adapter that isolates stream IDs to one transport session."""

    def __init__(self, event_store: EventStore, session_id: str):
        self._event_store = event_store
        self._stream_prefix = f"{len(session_id)}:{session_id}:"

    def _scope_stream_id(self, stream_id: StreamId) -> StreamId:
        return f"{self._stream_prefix}{stream_id}"

    def _unscope_stream_id(self, stream_id: StreamId) -> StreamId | None:
        if not stream_id.startswith(self._stream_prefix):
            return None
        return stream_id[len(self._stream_prefix) :]

    async def store_event(
        self, stream_id: StreamId, message: JSONRPCMessage | None
    ) -> EventId:
        return await self._event_store.store_event(
            self._scope_stream_id(stream_id), message
        )

    async def replay_events_after(
        self,
        last_event_id: EventId,
        send_callback: EventCallback,
    ) -> StreamId | None:
        replayed_events: list[EventMessage] = []

        async def buffer_event(event: EventMessage) -> None:
            replayed_events.append(event)

        scoped_stream_id = await self._event_store.replay_events_after(
            last_event_id, buffer_event
        )
        if scoped_stream_id is None:
            return None

        stream_id = self._unscope_stream_id(scoped_stream_id)
        if stream_id is None:
            logger.warning(
                "Event ID %s does not belong to this session-scoped event store",
                last_event_id,
            )
            return None

        for event in replayed_events:
            await send_callback(event)

        return stream_id
