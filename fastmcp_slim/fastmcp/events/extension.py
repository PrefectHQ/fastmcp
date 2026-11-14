"""FastMCP server implementation of the MCP Events extension, poll delivery."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeAlias

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import MCP_NAME_HEADER, decode_header_value
from mcp_types import HEADER_MISMATCH, INVALID_PARAMS
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.events._constants import EVENTS_EXTENSION_ID, NOT_FOUND
from fastmcp.events.models import (
    EventOccurrence,
    EventType,
    ListEventsParams,
    ListEventsResult,
    PollEventsParams,
    PollEventsResult,
)
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.mixins.mcp_operations import _apply_pagination

_EVENTS_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)


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
    """Serve event types over SEP-3415 `events/list` and `events/poll`.

    Each event type is one check function that reports what happened since a
    cursor. A poll carries everything the function needs, so the server keeps
    no subscription state and runs unchanged behind a stateless deployment.

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

    def __init__(self, *, poll_interval_ms: int = 30_000) -> None:
        if poll_interval_ms < 0:
            raise ValueError("poll_interval_ms must not be negative")
        self._poll_interval_ms = poll_interval_ms
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
            delivery=["poll"],
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

    async def _handle_poll(
        self,
        context: ServerRequestContext[Any, Any],
        params: PollEventsParams,
    ) -> PollEventsResult:
        # The SDK checks Mcp-Name only for the core protocol's named methods.
        headers = get_http_headers(include_all=True)
        if headers and decode_header_value(headers.get(MCP_NAME_HEADER)) != params.name:
            raise MCPError(
                code=HEADER_MISMATCH,
                message=f"{MCP_NAME_HEADER} header does not match the event name",
            )
        registration = self._registrations.get(params.name)
        if registration is None:
            raise MCPError(
                code=NOT_FOUND,
                message=f"Unknown event: {params.name}",
                data={"kind": "event"},
            )
        arguments = params.arguments or {}
        if registration.validator is not None:
            error = next(iter(registration.validator.iter_errors(arguments)), None)
            if error is not None:
                raise MCPError(
                    code=INVALID_PARAMS,
                    message=f"Invalid arguments for {params.name}: {error.message}",
                )

        batch = registration.check(
            EventPoll(
                arguments=arguments,
                cursor=params.cursor,
                max_age_ms=params.max_age_ms,
                max_events=params.max_events,
            )
        )
        if inspect.isawaitable(batch):
            batch = await batch

        now = datetime.now(UTC)
        return PollEventsResult(
            events=[
                EventOccurrence(
                    event_id=event.event_id or uuid.uuid4().hex,
                    name=params.name,
                    timestamp=(event.timestamp or now).isoformat(),
                    data=event.data,
                    meta=event.meta,
                )
                for event in batch.events
            ],
            cursor=batch.cursor,
            truncated=batch.truncated,
            has_more=batch.has_more,
            next_poll_ms=(
                self._poll_interval_ms
                if batch.next_poll_ms is None
                else batch.next_poll_ms
            ),
        )
