"""FastMCP server implementation of the MCP Events extension (poll and push).

Experimental: this API may change in any release without a deprecation period.
"""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar, cast

import anyio
import anyio.lowlevel
import mcp_types
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import (
    MCP_NAME_HEADER,
    decode_header_value,
    find_invalid_x_mcp_header,
    validate_mcp_param_headers,
)
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import HEADER_MISMATCH, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel

from fastmcp.experimental.events._constants import (
    EVENTS_EXTENSION_ID,
    FORBIDDEN,
    NOT_FOUND,
    UNSUPPORTED,
)
from fastmcp.experimental.events.models import (
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
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.experimental.events.emitted import EmittedEvents
    from fastmcp.experimental.events.log import EventLog

logger = get_logger(__name__)

_EVENTS_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_MIN_HEARTBEAT_MS = 1_000
_MAX_HEARTBEAT_MS = 60_000
# A check that fails with one of these mid-stream has ended the subscription
# (access revoked, event type gone), so the stream is terminated, not retried.
_TERMINAL_CODES = frozenset({NOT_FOUND, FORBIDDEN, UNSUPPORTED})


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
EventSource = TypeVar("EventSource", bound=EventCheck | type[BaseModel])


@dataclass(frozen=True)
class _Registration:
    descriptor: EventType
    check: EventCheck
    validator: Validator | None
    emitted: EmittedEvents | None = None


class EventsExtension(ServerExtension):
    """Serve event types over SEP-3415 poll and push delivery.

    Experimental: this API may change in any release without a deprecation
    period.

    Each event type is one check function that reports what happened since a
    cursor. `events/poll` calls it once per request, so the server keeps no
    subscription state. `events/stream` calls it on a loop for as long as the
    client holds the request open; pass `push=False` on a host that cannot
    keep a response stream open.

    ```python
    from fastmcp import FastMCP
    from fastmcp.experimental.events import Event, EventBatch, EventPoll, EventsExtension

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

    def __init__(
        self,
        *,
        poll_interval_ms: int = 30_000,
        push: bool = True,
        emit_buffer: int = 1_000,
        log: EventLog | None = None,
    ) -> None:
        """
        Args:
            poll_interval_ms: How long clients wait between polls, and how
                often a push stream re-runs a check function.
            push: Whether to offer `events/stream`.
            emit_buffer: How many emitted events per type the default
                in-memory log keeps. Ignored when `log` is given.
            log: Where emitted events are kept. Defaults to an
                `InMemoryEventLog`; pass a `DocketEventLog` to keep them in
                Redis, shared across replicas and restarts.
        """
        from fastmcp.experimental.events.log import InMemoryEventLog

        if poll_interval_ms < 0:
            raise ValueError("poll_interval_ms must not be negative")
        self._poll_interval_ms = poll_interval_ms
        self._push = push
        self._log = log if log is not None else InMemoryEventLog(emit_buffer)
        self._registrations: dict[str, _Registration] = {}
        self._models: dict[type[BaseModel], str] = {}

    def event(
        self,
        name: str,
        *,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        payload_schema: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Callable[[EventSource], EventSource]:
        """Register an event type, from a payload model or a check function.

        Decorating a Pydantic model declares an event type you `emit()`: the
        model's fields are the payload schema, its docstring the description,
        and the keyword parameters of its optional `matches` method the
        subscription arguments. FastMCP buffers what you emit and serves poll
        and push from that buffer.

        Decorating a check function serves an upstream you can read from a
        cursor: it receives an `EventPoll` and returns an `EventBatch`.

        Args:
            name: Event type name clients subscribe to.
            description: What the event means and when it fires. Defaults to
                a model's docstring.
            input_schema: JSON Schema for subscription arguments. Polls whose
                arguments do not match are rejected before the check runs.
                Derived from `matches` for a model.
            payload_schema: JSON Schema for each event's `data`. Derived from
                the fields for a model.
            meta: Optional `_meta` for the listing.
        """
        if name in self._registrations:
            raise ValueError(f"An event named {name!r} is already registered.")
        if input_schema is not None:
            # Fail at the decorator line, before there is anything to register.
            _schema_validator(name, input_schema)

        def register(source: EventSource) -> EventSource:
            if name in self._registrations:
                raise ValueError(f"An event named {name!r} is already registered.")
            emitted = None
            schema, payload, summary = input_schema, payload_schema, description
            if isinstance(source, type) and issubclass(source, BaseModel):
                from fastmcp.experimental.events.emitted import EmittedEvents

                if source in self._models:
                    raise ValueError(
                        f"{source.__name__} is already the event {self._models[source]!r}."
                    )
                emitted = EmittedEvents(name, source, self._log)
                schema = schema if schema is not None else emitted.input_schema
                payload = payload if payload is not None else emitted.payload_schema
                summary = summary or inspect.getdoc(source)
                check: EventCheck = emitted.check
            elif callable(source):
                check = cast(EventCheck, source)
            else:
                raise TypeError(
                    "events.event() decorates a check function or a Pydantic model"
                )
            validator = None if schema is None else _schema_validator(name, schema)
            descriptor = EventType(
                name=name,
                description=summary,
                delivery=["poll", "push"] if self._push else ["poll"],
                input_schema=schema,
                payload_schema=payload,
                meta=meta,
            )
            self._registrations[name] = _Registration(
                descriptor, check, validator, emitted
            )
            if emitted is not None:
                self._models[emitted.model] = name
            return source

        return register

    async def emit(
        self,
        payload: BaseModel,
        *,
        event_id: str | None = None,
        timestamp: datetime | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Record that an event happened, for every subscriber whose `matches` accepts it.

        `payload` is an instance of a model registered with `event()`. Pass the
        upstream's stable identifier as `event_id` when there is one, so a
        client that sees the same occurrence twice can drop the repeat.
        Open push streams for the event type wake at once; polls see it on
        their next request.
        """
        name = self._models.get(type(payload))
        if name is None:
            raise ValueError(
                f"{type(payload).__name__} is not a registered event model; "
                "decorate it with events.event()."
            )
        emitted = self._registrations[name].emitted
        assert emitted is not None
        await emitted.append(payload, event_id=event_id, timestamp=timestamp, meta=meta)

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        return self._log.lifespan()

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
        schema = registration.descriptor.input_schema
        if headers and schema is not None:
            rejection = validate_mcp_param_headers(schema, arguments, headers)
            if rejection is not None:
                raise MCPError(code=HEADER_MISMATCH, message=rejection.message)
        if registration.validator is not None:
            error = next(iter(registration.validator.iter_errors(arguments)), None)
            if error is not None:
                raise MCPError(
                    code=INVALID_PARAMS,
                    message=f"Invalid arguments for {name}: {error.message}",
                )
        return registration, arguments

    async def _check(self, registration: _Registration, poll: EventPoll) -> EventBatch:
        if is_coroutine_function(registration.check):
            batch = registration.check(poll)
        else:
            # Like sync tools, a sync check runs off the event loop so that
            # blocking I/O in it does not stall every other request.
            batch = await call_sync_fn_in_threadpool(registration.check, poll)
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
        handler, or until a check fails with an error that ends the
        subscription (`NotFound`, `Forbidden`, `Unsupported`), which is sent as
        `notifications/events/terminated`. Between checks the stream carries a
        heartbeat whenever the granted interval passes with nothing else sent.
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

        async def send(
            kind: str, *, event_meta: dict[str, Any] | None = None, **fields: Any
        ) -> None:
            nonlocal last_sent
            # An event's own `_meta` rides along, but the subscription ID is
            # what routes the frame, so it wins on a clash.
            frame_meta = {**event_meta, **meta} if event_meta else meta
            notification = StreamNotification(
                method=f"notifications/events/{kind}",
                params=StreamFrame(meta=frame_meta, **fields),
            )
            # The session only needs a model that dumps to a method and params.
            await context.session.send_notification(
                cast("mcp_types.ServerNotification", notification),
                related_request_id=subscription_id,
            )
            last_sent = time.monotonic()

        emitted = registration.emitted
        batch = await self._check(
            registration,
            EventPoll(
                arguments=arguments, cursor=params.cursor, max_age_ms=params.max_age_ms
            ),
        )
        # A backlog's cursor is the position after all of it, so a client that
        # stops partway must still hold the position it asked from.
        resumable = batch.events and params.cursor is not None
        cursor = params.cursor if resumable else batch.cursor
        await send(
            "active",
            cursor=cursor,
            truncated=batch.truncated,
            heartbeat_ms=heartbeat_ms,
        )
        while True:
            # A check that never awaits must still let cancellation and other
            # requests in, whatever interval it asks for.
            await anyio.lowlevel.checkpoint()
            for position, event in enumerate(batch.events, start=1):
                occurrence = self._occurrence(params.name, event)
                # Only the batch's cursor is known, so it rides the last event;
                # earlier ones keep the position before the batch.
                after = batch.cursor if position == len(batch.events) else cursor
                await send(
                    "event",
                    cursor=after,
                    event_meta=occurrence.meta,
                    **occurrence.model_dump(
                        by_alias=True, exclude_none=True, exclude={"meta"}
                    ),
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
                    if emitted is None:
                        await anyio.sleep(min(remaining, until_heartbeat))
                    elif await emitted.wait(cursor, min(remaining, until_heartbeat)):
                        break
            try:
                batch = await self._check(
                    registration, EventPoll(arguments=arguments, cursor=cursor)
                )
            except MCPError as error:
                payload = error.error.model_dump(exclude_none=True)
                if error.error.code in _TERMINAL_CODES:
                    await send("terminated", error=payload)
                    return StreamEventsResult(meta=meta)
                await send("error", error=payload)
                batch = EventBatch(cursor=cursor)
            except Exception:
                logger.exception("Check for event %r failed", params.name)
                await send(
                    "error", error={"code": INTERNAL_ERROR, "message": "Internal error"}
                )
                batch = EventBatch(cursor=cursor)
            if batch.truncated:
                # As on the first batch: a gap whose events are still to come
                # keeps the position before them, so a client that persists
                # this cursor and stops never skips the batch.
                await send(
                    "active",
                    cursor=cursor if batch.events else batch.cursor,
                    truncated=True,
                    heartbeat_ms=heartbeat_ms,
                )


def _schema_validator(name: str, schema: dict[str, Any]) -> Validator:
    """A validator for an event type's input schema, refusing invalid headers."""
    Draft202012Validator.check_schema(schema)
    reason = find_invalid_x_mcp_header(schema)
    if reason is not None:
        raise ValueError(f"Event {name!r} has an invalid x-mcp-header: {reason}")
    return Draft202012Validator(schema)
