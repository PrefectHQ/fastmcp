"""Client side of the MCP Events extension: the capability and push routing."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.extension import ClientExtension, NotificationBinding
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import RequestId

from fastmcp.events._constants import EVENTS_EXTENSION_ID
from fastmcp.events.models import StreamFrame
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

    def notifications(self) -> Sequence[NotificationBinding[Any]]:
        return [
            NotificationBinding(
                method=f"notifications/events/{kind}",
                params_type=StreamFrame,
                handler=partial(self._route, kind),
            )
            for kind in _STREAM_KINDS
        ]

    def _open(self, subscription_id: RequestId) -> MemoryObjectReceiveStream[Frame]:
        send, receive = anyio.create_memory_object_stream[Frame](_MAX_PENDING_FRAMES)
        self._streams[subscription_id] = send
        return receive

    def _close(self, subscription_id: RequestId) -> None:
        send = self._streams.pop(subscription_id, None)
        if send is not None:
            send.close()

    async def _route(self, kind: str, frame: StreamFrame) -> None:
        send = self._streams.get(frame.meta.get(SUBSCRIPTION_ID_META_KEY))
        if send is None:
            return
        try:
            send.send_nowait((kind, frame))
        except anyio.WouldBlock:
            logger.warning("Event stream backlog is full; ending the stream")
            self._close(frame.meta[SUBSCRIPTION_ID_META_KEY])
        except anyio.ClosedResourceError:
            pass
