"""Client side of the MCP Events extension: the capability and push routing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.extension import ClientExtension, NotificationBinding
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import RequestId
from pydantic import ValidationError

from fastmcp.experimental.events._constants import EVENTS_EXTENSION_ID
from fastmcp.experimental.events.models import StreamFrame
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_STREAM_KINDS = ("active", "event", "heartbeat", "error", "terminated")
_STREAM_METHOD_PREFIX = "notifications/events/"
_MAX_PENDING_FRAMES = 1024

#: One `notifications/events/*` message: its kind and its params.
Frame = tuple[str, StreamFrame]


class _OrderedIntercept:
    """A dispatcher's notification intercept, with Events routing in front."""

    def __init__(
        self,
        extension: EventsClientExtension,
        previous: Callable[[str, Mapping[str, Any] | None], bool] | None,
    ) -> None:
        self.extension = extension
        self.previous = previous

    def __call__(self, method: str, params: Mapping[str, Any] | None) -> bool:
        if self.extension._route_in_order(method, params):
            return True
        return self.previous(method, params) if self.previous is not None else False


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
        # The fallback for a session `_attach` could not hook: the SDK queues
        # each method separately, so frames of different kinds can arrive here
        # out of wire order.
        return [
            NotificationBinding(
                method=f"{_STREAM_METHOD_PREFIX}{kind}",
                params_type=StreamFrame,
                handler=partial(self._route_bound, kind),
            )
            for kind in _STREAM_KINDS
        ]

    def _attach(self, dispatcher: Any) -> None:
        """Route stream frames on `dispatcher`'s receive path, in wire order.

        The SDK delivers each notification method through its own queue and
        task, so a heartbeat can overtake the events sent before it, and its
        cursor would then skip them. The dispatcher's notification intercept
        runs synchronously in receive order, as the SDK's own
        `subscriptions/listen` routing relies on.
        """
        if not hasattr(dispatcher, "_on_notify_intercept"):
            logger.warning(
                "This MCP SDK offers no notification intercept; push frames of "
                "different kinds may arrive out of order"
            )
            return
        previous = dispatcher._on_notify_intercept
        if isinstance(previous, _OrderedIntercept) and previous.extension is self:
            return
        dispatcher._on_notify_intercept = _OrderedIntercept(self, previous)

    def _route_in_order(self, method: str, params: Mapping[str, Any] | None) -> bool:
        """Route a frame for an open stream; leave anything else to the SDK."""
        kind = method.removeprefix(_STREAM_METHOD_PREFIX)
        if kind == method or kind not in _STREAM_KINDS or params is None:
            return False
        meta = params.get("_meta")
        if not isinstance(meta, Mapping):
            return False
        subscription_id = meta.get(SUBSCRIPTION_ID_META_KEY)
        if not isinstance(subscription_id, str | int) or (
            subscription_id not in self._streams
        ):
            return False
        try:
            frame = StreamFrame.model_validate(params)
        except ValidationError:
            return False
        self._route(kind, frame)
        return True

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

    async def _route_bound(self, kind: str, frame: StreamFrame) -> None:
        self._route(kind, frame)

    def _route(self, kind: str, frame: StreamFrame) -> None:
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
