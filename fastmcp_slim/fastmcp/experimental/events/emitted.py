"""Event types declared as a payload model and fed by `EventsExtension.emit`.

Experimental: this API may change in any release without a deprecation period.

This is the SEP-3415 "emit-only" pattern: the server learns of an event when
code calls `emit()`, not by reading a cursor-addressable upstream. Emitted
events go to an `EventLog`, and poll and push both read from it. The default
log is a bounded in-memory buffer per event type, scoped to the process; a
`DocketEventLog` keeps it in Redis instead.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel, ValidationError, validate_call

from fastmcp.experimental.events.extension import Event, EventBatch, EventPoll
from fastmcp.experimental.events.log import EventLog, LoggedEvent
from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.json_schema import compress_schema

# How many logged events one poll reads at a time while it filters.
_READ_CHUNK = 500


class EmittedEvents:
    """The filter behind one event type declared as a model, over its log.

    `matches`, when the model defines it, is an instance method whose keyword
    parameters are the subscription arguments. It runs against each logged
    event for each subscription, so filtering costs no per-subscriber state.
    """

    def __init__(self, name: str, model: type[BaseModel], log: EventLog) -> None:
        self.name = name
        self.model = model
        self.log = log
        self.input_schema, self._matcher = parse_matches(model)

    @property
    def payload_schema(self) -> dict[str, Any]:
        return compress_schema(self.model.model_json_schema(), prune_titles=True)

    async def append(
        self,
        payload: BaseModel,
        *,
        event_id: str | None,
        timestamp: datetime | None,
        meta: dict[str, Any] | None,
    ) -> None:
        event = Event(
            data=payload.model_dump(mode="json"),
            # Generated once here, so every poll and stream sees the same ID.
            event_id=event_id or uuid.uuid4().hex,
            timestamp=timestamp or datetime.now(UTC),
            meta=meta,
        )
        await self.log.append(self.name, LoggedEvent(event, payload))

    async def wait(self, cursor: str | None, timeout: float) -> bool:
        """Wait up to `timeout` seconds for an emit after `cursor`."""
        return await self.log.wait(self.name, cursor, timeout)

    async def check(self, poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=await self.log.head(self.name))
        floor = None
        if poll.max_age_ms is not None:
            floor = datetime.now(UTC) - timedelta(milliseconds=poll.max_age_ms)
        limit = poll.max_events
        events: list[Event] = []
        cursor, truncated = poll.cursor, False
        while True:
            read = await self.log.read(self.name, cursor, count=_READ_CHUNK)
            truncated = truncated or read.truncated
            before = read.start
            for after, item in read.entries:
                if limit is not None and len(events) >= limit:
                    # Resume at the first event this batch did not look at.
                    return EventBatch(
                        events=events, cursor=before, has_more=True, truncated=truncated
                    )
                before = after
                timestamp = item.event.timestamp
                if floor is not None and timestamp is not None and timestamp < floor:
                    truncated = True
                    continue
                if self._matches(item, poll.arguments):
                    events.append(item.event)
            cursor = read.cursor
            if not read.more:
                return EventBatch(events=events, cursor=cursor, truncated=truncated)

    def _matches(self, item: LoggedEvent, arguments: dict[str, Any]) -> bool:
        if self._matcher is None:
            return True
        payload = item.payload
        if payload is None:
            payload = self.model.model_validate(item.event.data)
        try:
            return bool(self._matcher(payload, **arguments))
        except ValidationError as error:
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid arguments: {error.errors()[0]['msg']}",
            ) from None


def parse_matches(
    model: type[BaseModel],
) -> tuple[dict[str, Any] | None, Callable[..., Any] | None]:
    """The input schema and validating matcher from a model's `matches`, if any."""
    matches = model.__dict__.get("matches")
    if matches is None:
        return None, None
    if not inspect.isfunction(matches):
        raise TypeError(f"{model.__name__}.matches must be a plain instance method")
    if inspect.iscoroutinefunction(matches):
        raise TypeError(f"{model.__name__}.matches must not be async")
    parsed = ParsedFunction.from_function(_without_self(matches))
    return parsed.input_schema, validate_call(matches)


def _without_self(method: Callable[..., Any]) -> Callable[..., Any]:
    """`method` as a function of its keyword parameters, for schema parsing."""
    # Resolve string annotations in the method's own module, since the
    # stand-in below lives in this one.
    hints = get_type_hints(method, include_extras=True)
    signature = inspect.signature(method)
    parameters = [
        parameter.replace(annotation=hints.get(parameter.name, parameter.annotation))
        for parameter in list(signature.parameters.values())[1:]
    ]

    def parameters_only(**kwargs: Any) -> Any:
        raise NotImplementedError

    parameters_only.__signature__ = signature.replace(  # ty: ignore[missing-attribute]
        parameters=parameters, return_annotation=inspect.Signature.empty
    )
    parameters_only.__name__ = getattr(method, "__name__", "matches")
    parameters_only.__doc__ = method.__doc__
    parameters_only.__annotations__ = {
        parameter.name: parameter.annotation for parameter in parameters
    }
    return parameters_only
