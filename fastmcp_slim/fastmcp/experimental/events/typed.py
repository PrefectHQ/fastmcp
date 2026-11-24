"""Event types declared as a Pydantic model.

Experimental: this API may change in any release without a deprecation period.

The model's fields are the payload schema. Its optional `for_subscription`
method takes the subscription arguments as keyword parameters and decides, per
subscription and per event, what that subscription receives: the event as is,
a changed copy, or nothing. Events come from `EventsExtension.emit`, kept in an
`EventLog` that poll and push read from.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel, ValidationError, validate_call

from fastmcp.experimental.events.extension import Event, EventBatch, EventPoll
from fastmcp.experimental.events.log import EventLog
from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.json_schema import compress_schema

# How many logged events one poll reads at a time while it filters.
_READ_CHUNK = 500


class TypedEvent:
    """One event type declared as a model, served from its log."""

    def __init__(self, name: str, model: type[BaseModel], log: EventLog) -> None:
        self.name = name
        self.model = model
        self.log = log
        self.input_schema: dict[str, Any] | None = None
        self._hook: Callable[..., Any] | None = None
        self._arguments: Callable[..., dict[str, Any]] = lambda **kwargs: kwargs
        hook = model.__dict__.get("for_subscription")
        if hook is not None:
            if not inspect.isfunction(hook):
                raise TypeError(
                    f"{model.__name__}.for_subscription must be a plain instance method"
                )
            if inspect.iscoroutinefunction(hook):
                raise TypeError(f"{model.__name__}.for_subscription must not be async")
            parameters = _without_self(hook, model)
            self.input_schema = ParsedFunction.from_function(parameters).input_schema
            self._arguments = validate_call(parameters)
            self._hook = hook

    @property
    def payload_schema(self) -> dict[str, Any]:
        return compress_schema(self.model.model_json_schema(), prune_titles=True)

    async def emit(
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
        await self.log.append(self.name, event)

    async def wait(self, cursor: str | None, timeout: float) -> bool:
        """Wait up to `timeout` seconds for an emit after `cursor`."""
        return await self.log.wait(self.name, cursor, timeout)

    def arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A subscription's arguments, validated and coerced for the hook."""
        try:
            return self._arguments(**arguments)
        except ValidationError as error:
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid arguments: {error.errors()[0]['msg']}",
            ) from None

    def for_subscription(self, event: Event, arguments: dict[str, Any]) -> Event | None:
        """What a subscription with these validated arguments receives of `event`."""
        if self._hook is None:
            return event
        # A fresh instance from the logged data on every call: the hook sees
        # the event as emitted, and a change it makes reaches only this
        # subscription.
        result = self._hook(self.model.model_validate(event.data), **arguments)
        if result is None:
            return None
        if not isinstance(result, self.model):
            raise TypeError(
                f"{self.model.__name__}.for_subscription must return a "
                f"{self.model.__name__} or None, not {type(result).__name__}"
            )
        return replace(event, data=result.model_dump(mode="json"))

    async def check(self, poll: EventPoll) -> EventBatch:
        # Checked on the first poll too, which reads nothing.
        arguments = self.arguments(poll.arguments)
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
            for after, event in read.entries:
                if limit is not None and len(events) >= limit:
                    # Resume at the first event this batch did not look at.
                    return EventBatch(
                        events=events, cursor=before, has_more=True, truncated=truncated
                    )
                before = after
                if floor is not None and event.timestamp and event.timestamp < floor:
                    truncated = True
                    continue
                delivered = self.for_subscription(event, arguments)
                if delivered is not None:
                    events.append(delivered)
            cursor = read.cursor
            if not read.more:
                return EventBatch(events=events, cursor=cursor, truncated=truncated)


def _without_self(
    method: Callable[..., Any], model: type[BaseModel]
) -> Callable[..., Any]:
    """`method` as a function of its keyword parameters, for schema parsing."""
    # Resolve string annotations in the method's own module, since the
    # stand-in below lives in this one. The return annotation usually names
    # the model, which the decorator sees before its module does.
    hints = get_type_hints(method, localns={model.__name__: model}, include_extras=True)
    signature = inspect.signature(method)
    parameters = [
        parameter.replace(annotation=hints.get(parameter.name, parameter.annotation))
        for parameter in list(signature.parameters.values())[1:]
    ]

    def parameters_only(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    parameters_only.__signature__ = signature.replace(  # ty: ignore[missing-attribute]
        parameters=parameters, return_annotation=inspect.Signature.empty
    )
    parameters_only.__name__ = getattr(method, "__name__", "for_subscription")
    parameters_only.__doc__ = method.__doc__
    parameters_only.__annotations__ = {
        parameter.name: parameter.annotation for parameter in parameters
    }
    return parameters_only
