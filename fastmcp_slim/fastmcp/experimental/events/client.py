"""Client side of the MCP Events extension: the capability and push routing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.extension import ClientExtension, NotificationBinding
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import RequestId

from fastmcp.experimental.events._constants import EVENTS_EXTENSION_ID
from fastmcp.experimental.events.models import StreamFrame
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_STREAM_KINDS = ("active", "event", "heartbeat", "error", "terminated")
_MAX_PENDING_FRAMES = 1024

#: One `notifications/events/*` message: its kind and its params.
Frame = tuple[str, StreamFrame]


class EventsClientExtension(ClientExtension):
    """Declare support for the SEP-3415 Events extension.

    The instance also routes push notifications to the `events/stream` request
    they belong to, so use one instance per `Client`.
    """

    identifier = EVENTS_EXTENSION_ID

    def __init__(self) -> None:
        self._streams: dict[RequestId, MemoryObjectSendStream[Frame]] = {}
        # Per stream: stops the `events/stream` request when its buffer fills.
        self._stoppers: dict[RequestId, Callable[[], None]] = {}
        # Streams ended because their consumer fell behind.
        self._overflowed: set[RequestId] = set()
        # Per event name: argument path -> x-mcp-header token, from events/list.
        self._header_maps: dict[str, dict[tuple[str, ...], str]] = {}

    def notifications(self) -> Sequence[NotificationBinding[Any]]:
        return [
            NotificationBinding(
                method=f"notifications/events/{kind}",
                params_type=StreamFrame,
                handler=partial(self._route, kind),
            )
            for kind in _STREAM_KINDS
        ]

    def _open(
        self, subscription_id: RequestId, stop: Callable[[], None]
    ) -> MemoryObjectReceiveStream[Frame]:
        send, receive = anyio.create_memory_object_stream[Frame](_MAX_PENDING_FRAMES)
        self._streams[subscription_id] = send
        self._stoppers[subscription_id] = stop
        return receive

    def _close(self, subscription_id: RequestId) -> None:
        self._stoppers.pop(subscription_id, None)
        send = self._streams.pop(subscription_id, None)
        if send is not None:
            send.close()

    def _take_overflow(self, subscription_id: RequestId) -> bool:
        """Whether the stream ended because its backlog filled up."""
        if subscription_id in self._overflowed:
            self._overflowed.discard(subscription_id)
            return True
        return False

    async def _route(self, kind: str, frame: StreamFrame) -> None:
        send = self._streams.get(frame.meta.get(SUBSCRIPTION_ID_META_KEY))
        if send is None:
            return
        try:
            send.send_nowait((kind, frame))
        except anyio.WouldBlock:
            logger.warning("Event stream backlog is full; ending the subscription")
            subscription_id = frame.meta[SUBSCRIPTION_ID_META_KEY]
            self._overflowed.add(subscription_id)
            stop = self._stoppers.get(subscription_id)
            self._close(subscription_id)
            if stop is not None:
                stop()
        except anyio.ClosedResourceError:
            pass
