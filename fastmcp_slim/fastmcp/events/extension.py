"""FastMCP server implementation of the MCP Events extension (poll and push)."""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeAlias, cast

import anyio
import mcp_types
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import HEADER_MISMATCH, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.events._constants import EVENTS_EXTENSION_ID, NOT_FOUND, UNSUPPORTED
from fastmcp.events.models import (
    EventOccurrence,
    EventType,
    ListEventsParams,
    ListEventsResult,
    PollEventsParams,
    PollEventsResult,
    StreamEventsParams,
    StreamEventsResult,
    StreamFrame,
    StreamNotification,
)
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.mixins.mcp_operations import _apply_pagination
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_EVENTS_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_MIN_HEARTBEAT_MS = 1_000
_MAX_HEARTBEAT_MS = 60_000


@dataclass
class Event:
    """An event a check function reports.

    Set `event_id` to the upstream's stable identifier whenever it has one:
    clients deduplicate on it, and a generated ID differs on every poll.
    """

    data: dict[str, Any]
    event_id: str | None = None
    timestamp: datetime | None = None
    meta: dict[str, Any] | None = None


@dataclass
class EventBatch:
    """What a check function returns for one poll.

    `cursor` is the position after `events`, returned to the client and passed
    back on its next poll. Leave it `None` for an event type with no replay.
    """

    events: Sequence[Event] = ()
    cursor: str | None = None
    truncated: bool = False
    has_more: bool = False
    next_poll_ms: int | None = None


@dataclass(frozen=True)
class EventPoll:
    """One `events/poll` request, as a check function receives it.

    A `cursor` of `None` means "start from now": return no events and the
    current position.
    """

    arguments: dict[str, Any] = field(default_factory=dict)
    cursor: str | None = None
    max_age_ms: int | None = None
    max_events: int | None = None


EventCheck: TypeAlias = Callable[[EventPoll], EventBatch | Awaitable[EventBatch]]


@dataclass(frozen=True)
class _Registration:
    descriptor: EventType
    check: EventCheck
    validator: Validator | None


class EventsExtension(ServerExtension):
    """Serve event types over SEP-3415 poll and push delivery.

    Each event type is one check function that reports what happened since a
    cursor. `events/poll` calls it once per request, so the server keeps no
    subscription state. `events/stream` calls it on a loop for as long as the
    client holds the request open; pass `push=False` on a host that cannot
    keep a response stream open.

    ```python
    from fastmcp import FastMCP
    from fastmcp.events import Event, EventBatch, EventPoll, EventsExtension

    mcp = FastMCP("alerts")
    events = EventsExtension()
    mcp.add_extension(events)


    @events.event(name="alert.fired", description="An alert fired")
    async def alert_fired(poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=await newest_alert_id())
        alerts = await alerts_after(poll.cursor)
        return EventBatch(
            events=[Event(data=a.payload, event_id=a.id) for a in alerts],
            cursor=alerts[-1].id if alerts else poll.cursor,
        )
    ```
    """

    identifier = EVENTS_EXTENSION_ID

    def __init__(self, *, poll_interval_ms: int = 30_000, push: bool = True) -> None:
        if poll_interval_ms < 0:
            raise ValueError("poll_interval_ms must not be negative")
        self._poll_interval_ms = poll_interval_ms
        self._push = push
        self._registrations: dict[str, _Registration] = {}

    def event(
        self,
        *,
        name: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        payload_schema: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Callable[[EventCheck], EventCheck]:
        """Register a check function as an event type.

        Args:
            name: Event type name clients subscribe to.
            description: What the event means and when it fires.
            input_schema: JSON Schema for subscription arguments. Polls whose
                arguments do not match are rejected before the check runs.
            payload_schema: JSON Schema for each event's `data`.
            meta: Optional `_meta` for the listing.
        """
        if name in self._registrations:
            raise ValueError(f"An event named {name!r} is already registered.")
        validator = None
        if input_schema is not None:
            Draft202012Validator.check_schema(input_schema)
            validator = Draft202012Validator(input_schema)
        descriptor = EventType(
            name=name,
            description=description,
            delivery=["poll", "push"] if self._push else ["poll"],
            input_schema=input_schema,
            payload_schema=payload_schema,
            meta=meta,
        )

        def register(check: EventCheck) -> EventCheck:
            self._registrations[name] = _Registration(descriptor, check, validator)
            return check

        return register

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(
                method="events/list",
                params_type=ListEventsParams,
                handler=self._handle_list,
                protocol_versions=_EVENTS_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="events/poll",
                params_type=PollEventsParams,
                handler=self._handle_poll,
                protocol_versions=_EVENTS_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="events/stream",
                params_type=StreamEventsParams,
                handler=self._handle_stream,
                protocol_versions=_EVENTS_METHOD_VERSIONS,
            ),
        )

    async def _handle_list(
        self,
        context: ServerRequestContext[Any, Any],
        params: ListEventsParams,
    ) -> ListEventsResult:
        descriptors = sorted(
            (r.descriptor for r in self._registrations.values()),
            key=lambda descriptor: descriptor.name,
        )
        page, next_cursor = _apply_pagination(
            descriptors, params.cursor, self.server._list_page_size
        )
        return ListEventsResult(events=page, next_cursor=next_cursor)

    def _subscription(
        self, name: str, arguments: dict[str, Any] | None
    ) -> tuple[_Registration, dict[str, Any]]:
        """The event type and arguments a poll or stream request names."""
        # The SDK checks Mcp-Name only for the core protocol's named methods.
        headers = get_http_headers(include_all=True)
        if headers and decode_header_value(headers.get(MCP_NAME_HEADER)) != name:
            raise MCPError(
                code=HEADER_MISMATCH,
                message=f"{MCP_NAME_HEADER} header does not match the event name",
            )
        registration = self._registrations.get(name)
        if registration is None:
            raise MCPError(
                code=NOT_FOUND,
                message=f"Unknown event: {name}",
                data={"kind": "event"},
            )
        arguments = arguments or {}
        if registration.validator is not None:
            error = next(iter(registration.validator.iter_errors(arguments)), None)
            if error is not None:
                raise MCPError(
                    code=INVALID_PARAMS,
                    message=f"Invalid arguments for {name}: {error.message}",
                )
        return registration, arguments

    async def _check(self, registration: _Registration, poll: EventPoll) -> EventBatch:
        batch = registration.check(poll)
        if inspect.isawaitable(batch):
            batch = await batch
        return batch

    def _occurrence(self, name: str, event: Event) -> EventOccurrence:
        return EventOccurrence(
            event_id=event.event_id or uuid.uuid4().hex,
            name=name,
            timestamp=(event.timestamp or datetime.now(UTC)).isoformat(),
            data=event.data,
            meta=event.meta,
        )

    async def _handle_poll(
        self,
        context: ServerRequestContext[Any, Any],
        params: PollEventsParams,
    ) -> PollEventsResult:
        registration, arguments = self._subscription(params.name, params.arguments)
        batch = await self._check(
            registration,
            EventPoll(
                arguments=arguments,
                cursor=params.cursor,
                max_age_ms=params.max_age_ms,
                max_events=params.max_events,
            ),
        )
        return PollEventsResult(
            events=[self._occurrence(params.name, event) for event in batch.events],
            cursor=batch.cursor,
            truncated=batch.truncated,
            has_more=batch.has_more,
            next_poll_ms=(
                self._poll_interval_ms
                if batch.next_poll_ms is None
                else batch.next_poll_ms
            ),
        )

    async def _handle_stream(
        self,
        context: ServerRequestContext[Any, Any],
        params: StreamEventsParams,
    ) -> StreamEventsResult:
        """Serve one push subscription by running its check function on a loop.

        The request stays open until the client goes away, which cancels this
        handler. Between checks the stream carries a heartbeat whenever the
        granted interval passes with nothing else sent.
        """
        if not self._push:
            raise MCPError(
                code=UNSUPPORTED,
                message="Push delivery is not offered by this server",
                data={"feature": "deliveryMode", "value": "push"},
            )
        # An HTTP app built with json_response=True answers each request with
        # one JSON body: notifications are dropped and this handler would
        # never return. The SDK exposes that only as an absent stream sink.
        outbound = getattr(context.session, "_request_outbound", None)
        if getattr(outbound, "sink", outbound) is None:
            raise MCPError(
                code=UNSUPPORTED,
                message="This server's HTTP responses cannot carry a stream",
                data={"feature": "deliveryMode", "value": "push"},
            )
        registration, arguments = self._subscription(params.name, params.arguments)
        subscription_id = context.request_id
        if subscription_id is None:
            raise MCPError(INVALID_REQUEST, "events/stream requires a request id")
        meta = {SUBSCRIPTION_ID_META_KEY: subscription_id}
        heartbeat_ms = min(
            max(params.heartbeat_ms or 30_000, _MIN_HEARTBEAT_MS), _MAX_HEARTBEAT_MS
        )
        last_sent = time.monotonic()

        async def send(kind: str, **fields: Any) -> None:
            nonlocal last_sent
            notification = StreamNotification(
                method=f"notifications/events/{kind}",
                params=StreamFrame(meta=meta, **fields),
            )
            # The session only needs a model that dumps to a method and params.
            await context.session.send_notification(
                cast("mcp_types.ServerNotification", notification),
                related_request_id=subscription_id,
            )
            last_sent = time.monotonic()

        batch = await self._check(
            registration,
            EventPoll(
                arguments=arguments, cursor=params.cursor, max_age_ms=params.max_age_ms
            ),
        )
        cursor = batch.cursor
        await send(
            "active",
            cursor=cursor,
            truncated=batch.truncated,
            heartbeat_ms=heartbeat_ms,
        )
        while True:
            for position, event in enumerate(batch.events, start=1):
                occurrence = self._occurrence(params.name, event)
                # Only the batch's cursor is known, so it rides the last event;
                # earlier ones keep the position before the batch.
                after = batch.cursor if position == len(batch.events) else cursor
                await send(
                    "event",
                    cursor=after,
                    **occurrence.model_dump(by_alias=True, exclude_none=True),
                )
            cursor = batch.cursor
            if not batch.has_more:
                interval_ms = (
                    self._poll_interval_ms
                    if batch.next_poll_ms is None
                    else batch.next_poll_ms
                )
                deadline = time.monotonic() + interval_ms / 1000
                while (remaining := deadline - time.monotonic()) > 0:
                    until_heartbeat = last_sent + heartbeat_ms / 1000 - time.monotonic()
                    if until_heartbeat <= 0:
                        await send("heartbeat", cursor=cursor)
                        continue
                    await anyio.sleep(min(remaining, until_heartbeat))
            try:
                batch = await self._check(
                    registration, EventPoll(arguments=arguments, cursor=cursor)
                )
            except MCPError as error:
                await send("error", error=error.error.model_dump(exclude_none=True))
                batch = EventBatch(cursor=cursor)
            except Exception:
                logger.exception("Check for event %r failed", params.name)
                await send(
                    "error", error={"code": INTERNAL_ERROR, "message": "Internal error"}
                )
                batch = EventBatch(cursor=cursor)
            if batch.truncated:
                await send(
                    "active",
                    cursor=batch.cursor,
                    truncated=True,
                    heartbeat_ms=heartbeat_ms,
                )
