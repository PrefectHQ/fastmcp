"""Event types declared as a payload model and fed by `EventsExtension.emit`.

Experimental: this API may change in any release without a deprecation period.

This is the SEP-3415 "emit-only" pattern: the server learns of an event when
code calls `emit()`, not by reading a cursor-addressable upstream. Emitted
events are kept in a bounded in-memory buffer per event type, and poll and push
both read from it. The cursor is a position in that buffer, scoped to the
process: after a restart, a client's old cursor is answered from the start of
the new buffer with `truncated=True`.
"""

from __future__ import annotations

import inspect
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints

import anyio
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel, ValidationError, validate_call

from fastmcp.experimental.events.extension import Event, EventBatch, EventPoll
from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.json_schema import compress_schema


@dataclass(frozen=True)
class _Emitted:
    position: int
    event: Event
    payload: BaseModel


class EmittedEvents:
    """The buffer and filter behind one event type declared as a model.

    `matches`, when the model defines it, is an instance method whose keyword
    parameters are the subscription arguments. It runs against each buffered
    event for each subscription, so filtering costs no per-subscriber state.
    """

    def __init__(self, model: type[BaseModel], *, max_events: int) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self.model = model
        self._epoch = uuid.uuid4().hex[:12]
        self._buffer: deque[_Emitted] = deque(maxlen=max_events)
        self._next = 0
        self._changed = anyio.Event()
        self._matcher: Callable[..., Any] | None = None
        self.input_schema: dict[str, Any] | None = None
        matches = model.__dict__.get("matches")
        if matches is not None:
            if not inspect.isfunction(matches):
                raise TypeError(
                    f"{model.__name__}.matches must be a plain instance method"
                )
            if inspect.iscoroutinefunction(matches):
                raise TypeError(f"{model.__name__}.matches must not be async")
            parsed = ParsedFunction.from_function(_without_self(matches))
            self.input_schema = parsed.input_schema
            self._matcher = validate_call(matches)

    @property
    def payload_schema(self) -> dict[str, Any]:
        return compress_schema(self.model.model_json_schema(), prune_titles=True)

    def changed(self) -> anyio.Event:
        """An event set by the next `append`, for push streams to wait on."""
        return self._changed

    def append(
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
        # A copy, so `matches` sees the payload as emitted even if the caller
        # changes its instance afterwards, the same as the `data` delivered.
        snapshot = payload.model_copy(deep=True)
        self._buffer.append(_Emitted(self._next, event, snapshot))
        self._next += 1
        changed, self._changed = self._changed, anyio.Event()
        changed.set()

    def _cursor(self, position: int) -> str:
        return f"{self._epoch}-{position}"

    def _start(self, cursor: str) -> tuple[int, bool]:
        """The first position after `cursor`, and whether events were skipped."""
        oldest = self._buffer[0].position if self._buffer else self._next
        epoch, _, raw = cursor.rpartition("-")
        if epoch != self._epoch or not raw.isdigit():
            # A cursor from another process (a restart, another replica) says
            # nothing about this buffer: replay what it has, as a gap.
            return oldest, True
        start = int(raw)
        if start > self._next:
            return self._next, True
        if start < oldest:
            return oldest, True
        return start, False

    def check(self, poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=self._cursor(self._next))
        start, truncated = self._start(poll.cursor)
        floor = None
        if poll.max_age_ms is not None:
            floor = datetime.now(UTC) - timedelta(milliseconds=poll.max_age_ms)
        limit = poll.max_events
        events: list[Event] = []
        for item in list(self._buffer):
            if item.position < start:
                continue
            if limit is not None and len(events) >= limit:
                # Resume at the first event this batch did not look at.
                return EventBatch(
                    events=events,
                    cursor=self._cursor(item.position),
                    has_more=True,
                    truncated=truncated,
                )
            timestamp = item.event.timestamp
            if floor is not None and timestamp is not None and timestamp < floor:
                truncated = True
                continue
            if self._matches(item.payload, poll.arguments):
                events.append(item.event)
        return EventBatch(
            events=events, cursor=self._cursor(self._next), truncated=truncated
        )

    def _matches(self, payload: BaseModel, arguments: dict[str, Any]) -> bool:
        if self._matcher is None:
            return True
        try:
            return bool(self._matcher(payload, **arguments))
        except ValidationError as error:
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid arguments: {error.errors()[0]['msg']}",
            ) from None


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
