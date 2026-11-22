"""Client functions for the MCP Events extension: list, poll and subscribe."""

from __future__ import annotations

import random
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import aclosing
from itertools import count
from typing import TYPE_CHECKING, Any, Literal, cast

import anyio
import mcp_types
from mcp.shared.dispatcher import CallOptions
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import (
    MCP_NAME_HEADER,
    encode_header_value,
    find_invalid_x_mcp_header,
    mcp_param_headers,
    x_mcp_header_map,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.telemetry import client_span
from fastmcp.experimental.events._constants import EVENTS_EXTENSION_ID, UNSUPPORTED
from fastmcp.experimental.events.client import EventsClientExtension
from fastmcp.experimental.events.models import (
    EventOccurrence,
    EventType,
    ListEventsParams,
    ListEventsRequest,
    ListEventsResult,
    PollEventsParams,
    PollEventsRequest,
    PollEventsResult,
    StreamEventsParams,
    StreamEventsRequest,
)
from fastmcp.telemetry import inject_trace_context
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.client.client import Client

logger = get_logger(__name__)

AUTO_PAGINATION_MAX_PAGES = 250
_REMEMBERED_EVENT_IDS = 1024
_ACTIVATION_TIMEOUT = 90.0
_stream_ids = count(1)

Delivery = Literal["poll", "push"]


class EventBacklogFullError(RuntimeError):
    """A push subscription's consumer fell so far behind that frames were lost.

    The subscription has ended. Its `cursor` is the position after the last
    event yielded, so resubscribing from it repeats events rather than
    skipping them. A consumer that cannot keep up should process events
    faster, or poll with `max_events`.
    """


class _SubscriptionTerminated(MCPError):
    """The server ended a push subscription with `notifications/events/terminated`."""


def _trace_meta() -> mcp_types.RequestParamsMeta | None:
    return cast("mcp_types.RequestParamsMeta | None", inject_trace_context(None))


class EventSubscription:
    """One subscription to an event type, iterated with `async for`.

    `cursor` is the position after the last event or poll. Persist it and pass
    it to `subscribe_events(client, ..., cursor=...)` to resume after a restart. `truncated`
    is set once the server reports that it skipped events the cursor asked for.
    """

    def __init__(
        self,
        client: Client,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        cursor: str | None,
        max_age_ms: int | None,
        max_events: int | None,
        min_poll_interval: float,
        delivery: Delivery | None,
    ) -> None:
        self._client = client
        self.name = name
        self.arguments = arguments
        self.cursor = cursor
        self.truncated = False
        self._max_age_ms = max_age_ms
        self._max_events = max_events
        self._min_poll_interval = min_poll_interval
        self._delivery = delivery
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _is_new(self, event: EventOccurrence) -> bool:
        if event.event_id in self._seen:
            return False
        self._seen[event.event_id] = None
        if len(self._seen) > _REMEMBERED_EVENT_IDS:
            self._seen.popitem(last=False)
        return True

    def _note_gap(self) -> None:
        self.truncated = True
        logger.warning(
            "[%s] Server skipped events for %r; the subscription has a gap",
            self._client.name,
            self.name,
        )

    async def _choose_delivery(self) -> Delivery:
        """Push when the event type offers it, as the SEP prefers; else poll."""
        for event_type in await list_events(self._client):
            if event_type.name == self.name and "push" in event_type.delivery:
                return "push"
        return "poll"

    async def __aiter__(self) -> AsyncGenerator[EventOccurrence, None]:
        if self._delivery is not None:
            # Listing records which arguments travel as Mcp-Param-* headers,
            # which the server requires on every poll and stream.
            if self.name not in _events_extension(self._client)._header_maps:
                await list_events(self._client)
            delivery = self._delivery
        else:
            delivery = await self._choose_delivery()
        if delivery == "push":
            try:
                async with aclosing(self._push()) as events:
                    async for event in events:
                        yield event
                return
            except MCPError as error:
                # An event type can list push on a server whose transport
                # cannot carry it, so a stream request refused as Unsupported
                # falls back unless the caller insisted. A stream the server
                # terminated has ended and is the application's to handle.
                if (
                    self._delivery == "push"
                    or error.error.code != UNSUPPORTED
                    or isinstance(error, _SubscriptionTerminated)
                ):
                    raise
        async with aclosing(self._poll()) as events:
            async for event in events:
                yield event

    async def _poll(self) -> AsyncGenerator[EventOccurrence, None]:
        while True:
            result = await poll_events_mcp(
                self._client,
                self.name,
                self.arguments,
                cursor=self.cursor,
                max_age_ms=self._max_age_ms,
                max_events=self._max_events,
            )
            if result.truncated:
                self._note_gap()
            # The cursor is the position after the whole batch, so it moves
            # with the batch's last event: a consumer that stops partway
            # resumes from the batch's start instead of skipping the rest.
            fresh = [event for event in result.events if self._is_new(event)]
            for event in fresh[:-1]:
                yield event
            self.cursor = result.cursor
            if fresh:
                yield fresh[-1]
            if not result.has_more:
                await anyio.sleep(
                    max(result.next_poll_ms / 1000, self._min_poll_interval)
                )

    async def _push(self) -> AsyncGenerator[EventOccurrence, None]:
        """Hold an `events/stream` request open, reopening it if it goes quiet."""
        client = self._client
        _require_events_extension(client)
        extension = _events_extension(client)
        session = client.session
        task_group = session._task_group
        if task_group is None:
            raise RuntimeError("The client session is not running")

        while True:
            request_id = f"events-{next(_stream_ids)}"
            request = StreamEventsRequest(
                params=StreamEventsParams(
                    name=self.name,
                    arguments=self.arguments,
                    cursor=self.cursor,
                    max_age_ms=self._max_age_ms,
                    meta=_trace_meta(),
                )
            )
            data = request.model_dump(by_alias=True, mode="json", exclude_none=True)
            options: CallOptions = {"request_id": request_id}
            session._stamp(data, options)
            headers = options.setdefault("headers", {})
            headers[MCP_NAME_HEADER] = encode_header_value(self.name)
            headers.update(_event_param_headers(client, self.name, self.arguments))

            frames = extension._open(request_id)
            failures: list[MCPError] = []
            scope = anyio.CancelScope()

            async def drive(
                data: dict[str, Any] = data,
                options: CallOptions = options,
                request_id: str = request_id,
                failures: list[MCPError] = failures,
                scope: anyio.CancelScope = scope,
            ) -> None:
                # No result timeout: the response arrives when the stream ends.
                with scope:
                    try:
                        await session._dispatcher.send_raw_request(
                            data["method"], data.get("params"), options
                        )
                    except MCPError as failure:
                        failures.append(failure)
                    finally:
                        extension._close(request_id)

            task_group.start_soon(drive)
            quiet_limit = _ACTIVATION_TIMEOUT
            went_quiet = False
            try:
                while True:
                    try:
                        with anyio.fail_after(quiet_limit):
                            kind, frame = await frames.receive()
                    except anyio.EndOfStream:
                        if extension._take_overflow(request_id):
                            raise EventBacklogFullError(
                                f"Event stream for {self.name!r} fell behind and "
                                "lost events; resubscribe from its cursor"
                            ) from None
                        break
                    except TimeoutError:
                        went_quiet = True
                        break
                    if kind == "active":
                        self.cursor = frame.cursor
                        if frame.truncated:
                            self._note_gap()
                        if frame.heartbeat_ms:
                            quiet_limit = 3 * frame.heartbeat_ms / 1000
                    elif kind == "heartbeat":
                        self.cursor = frame.cursor
                    elif kind == "event":
                        event = EventOccurrence.model_validate(frame.model_extra)
                        self.cursor = frame.cursor
                        if self._is_new(event):
                            yield event
                    elif kind == "error":
                        logger.warning(
                            "[%s] Event %r reported an error: %s",
                            client.name,
                            self.name,
                            frame.error,
                        )
                    elif kind == "terminated":
                        error = frame.error or {}
                        raise _SubscriptionTerminated(
                            code=error.get("code", mcp_types.INTERNAL_ERROR),
                            message=error.get("message", "Subscription terminated"),
                            data=error.get("data"),
                        )
            finally:
                scope.cancel()
                extension._close(request_id)
                extension._take_overflow(request_id)

            if failures:
                raise failures[0]
            if not went_quiet:
                return
            logger.warning(
                "[%s] Event stream for %r went quiet; reconnecting",
                client.name,
                self.name,
            )
            await anyio.sleep(random.uniform(0, 1))  # noqa: S311


def _events_extension(client: Client) -> EventsClientExtension:
    for extension in client._extensions_arg or ():
        if isinstance(extension, EventsClientExtension):
            return extension
    raise RuntimeError(
        "This client did not opt into the Events extension; pass "
        "EventsClientExtension() in Client(extensions=[...])"
    )


def _require_events_extension(client: Client) -> None:
    if client.protocol_version is None:
        raise RuntimeError(
            "Client is not connected. Use the 'async with client:' context "
            "manager first."
        )
    if client.protocol_version not in MODERN_PROTOCOL_VERSIONS:
        raise RuntimeError("The Events extension requires a modern MCP connection")
    client_extensions = client._session_kwargs.get("extensions") or {}
    if EVENTS_EXTENSION_ID not in client_extensions:
        raise RuntimeError(
            "This client did not opt into the Events extension; pass "
            "EventsClientExtension() in Client(extensions=[...])"
        )
    capabilities = client.server_capabilities
    server_extensions = capabilities.extensions if capabilities else None
    if not server_extensions or EVENTS_EXTENSION_ID not in server_extensions:
        raise RuntimeError("The server does not advertise the Events extension")


async def list_events_mcp(
    client: Client, *, cursor: str | None = None
) -> ListEventsResult:
    """Send one `events/list` request and return its protocol result.

    Args:
        cursor: Optional pagination cursor from a previous result.

    Raises:
        RuntimeError: If the client is disconnected or Events was not negotiated.
        MCPError: If the server rejects the request.
    """
    _require_events_extension(client)
    with client_span(
        "events/list",
        "events/list",
        "",
        session_id=client.transport.get_session_id(),
    ):
        request = ListEventsRequest(
            params=ListEventsParams(cursor=cursor, meta=_trace_meta())
        )
        result = await client._await_with_session_monitoring(
            client.session.send_request(request, ListEventsResult)
        )
    usable = []
    header_maps = _events_extension(client)._header_maps
    for event_type in result.events:
        # SEP-3415: an event type whose x-mcp-header annotations break the
        # transport's rules MUST be dropped from the listing.
        reason = find_invalid_x_mcp_header(event_type.input_schema)
        if reason is not None:
            logger.warning(
                "[%s] Dropping event %r: invalid x-mcp-header (%s)",
                client.name,
                event_type.name,
                reason,
            )
            continue
        header_maps[event_type.name] = x_mcp_header_map(event_type.input_schema)
        usable.append(event_type)
    result.events = usable
    return result


def _event_param_headers(
    client: Client, name: str, arguments: dict[str, Any] | None
) -> dict[str, str]:
    """`Mcp-Param-*` headers for an event's annotated arguments, once listed."""
    header_map = _events_extension(client)._header_maps.get(name)
    if not header_map or not arguments:
        return {}
    return mcp_param_headers(header_map, arguments)


async def list_events(
    client: Client, max_pages: int = AUTO_PAGINATION_MAX_PAGES
) -> list[EventType]:
    """Retrieve every event type the server offers.

    Args:
        max_pages: Maximum number of pages to fetch before raising.

    Raises:
        RuntimeError: If Events was not negotiated or the page limit is reached.
        MCPError: If the server rejects a request.
    """
    events: list[EventType] = []
    cursor: str | None = None
    for _ in range(max_pages):
        result = await list_events_mcp(client, cursor=cursor)
        events.extend(result.events)
        if result.next_cursor is None or result.next_cursor == cursor:
            return events
        cursor = result.next_cursor
    raise RuntimeError(
        f"[{client.name}] Reached auto-pagination limit ({max_pages} pages) "
        "for list_events. Use list_events_mcp() for manual pagination, "
        "or increase max_pages."
    )


async def poll_events_mcp(
    client: Client,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    cursor: str | None = None,
    max_age_ms: int | None = None,
    max_events: int | None = None,
) -> PollEventsResult:
    """Send one `events/poll` request and return its protocol result.

    Args:
        name: Event type to poll.
        arguments: Subscription arguments matching the event's input schema.
        cursor: Position from a previous poll. `None` starts from now: the
            server returns no events and a cursor for later polls.
        max_age_ms: Do not replay events older than this.
        max_events: Cap on the number of events returned.

    Raises:
        RuntimeError: If the client is disconnected or Events was not negotiated.
        MCPError: If the server rejects the request.
    """
    _require_events_extension(client)
    with client_span(
        f"events/poll {name}",
        "events/poll",
        name,
        session_id=client.transport.get_session_id(),
    ):
        request = PollEventsRequest(
            params=PollEventsParams(
                name=name,
                arguments=arguments,
                cursor=cursor,
                max_age_ms=max_age_ms,
                max_events=max_events,
                meta=_trace_meta(),
            )
        )
        param_headers = _event_param_headers(client, name, arguments)
        if not param_headers:
            return await client._await_with_session_monitoring(
                client.session.send_request(request, PollEventsResult)
            )
        return await client._await_with_session_monitoring(
            _send_with_headers(client, request, param_headers)
        )


async def _send_with_headers(
    client: Client, request: PollEventsRequest, extra_headers: dict[str, str]
) -> PollEventsResult:
    """Send a poll with `Mcp-Param-*` headers, which `send_request` cannot add."""
    session = client.session
    data = request.model_dump(by_alias=True, mode="json", exclude_none=True)
    options: CallOptions = {}
    session._stamp(data, options)
    headers = options.setdefault("headers", {})
    headers[MCP_NAME_HEADER] = encode_header_value(request.params.name)
    headers.update(extra_headers)
    if session._session_read_timeout_seconds is not None:
        options["timeout"] = session._session_read_timeout_seconds
    raw = await session._dispatcher.send_raw_request(
        data["method"], data.get("params"), options
    )
    return PollEventsResult.model_validate(raw)


def subscribe_events(
    client: Client,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    cursor: str | None = None,
    max_age_ms: int | None = None,
    max_events: int | None = None,
    min_poll_interval: float = 1.0,
    delivery: Delivery | None = None,
) -> EventSubscription:
    """Subscribe to one event type and iterate its events as they arrive.

    With push delivery the client holds an `events/stream` request open
    and the server writes events to it. With poll delivery the client asks
    on the interval the server recommends and drains a backlog without
    waiting. Either way events already yielded are dropped, and the
    subscription runs only while iterated: leaving the `async for` ends it.

    Args:
        name: Event type to subscribe to.
        arguments: Subscription arguments matching the event's input schema.
        cursor: Position to resume from. `None` starts from now.
        max_age_ms: Do not replay events older than this.
        max_events: Cap on the number of events per poll.
        min_poll_interval: Shortest wait between polls, in seconds, whatever
            the server recommends.
        delivery: `"push"` or `"poll"`. By default the subscription uses
            push when the event type offers it and poll otherwise.
    """
    return EventSubscription(
        client,
        name,
        arguments,
        cursor=cursor,
        max_age_ms=max_age_ms,
        max_events=max_events,
        min_poll_interval=min_poll_interval,
        delivery=delivery,
    )
