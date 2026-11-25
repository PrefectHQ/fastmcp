"""One event type a server offers, declared as a Pydantic model.

Experimental: this API may change in any release without a deprecation period.

The model's fields are the payload schema and its docstring the description.
Its optional `for_subscription` method takes the subscription arguments as
keyword parameters and decides, per subscription and per event, what that
subscription receives: the event as is, a changed copy, or nothing. Events
come from one of two sources: `EventsExtension.emit`, kept in an `EventLog`
that poll and push read from, or a reader registered with
`EventsExtension.reader` that reads an upstream's own history from a cursor.
"""

from __future__ import annotations

import copy
import inspect
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints

import anyio
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import find_invalid_x_mcp_header
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel, ValidationError, validate_call

from fastmcp.experimental.events.extension import (
    Event,
    EventBatch,
    EventPoll,
    EventReader,
)
from fastmcp.experimental.events.log import EventLog
from fastmcp.experimental.events.models import EventType
from fastmcp.tools.function_parsing import ParsedFunction
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.json_schema import compress_schema

# How many logged events one poll reads at a time while it filters.
_READ_CHUNK = 500


class EventRegistration:
    """One event type: its model, and the log or reader its events come from."""

    def __init__(
        self,
        name: str,
        model: type[BaseModel],
        log: EventLog,
        *,
        description: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError("events.event() decorates a Pydantic model")
        self.name = name
        self.model = model
        self.log = log
        self.description = description or inspect.getdoc(model)
        self.meta = meta
        self.input_schema: dict[str, Any] | None = None
        self.payload_schema = compress_schema(
            model.model_json_schema(mode="serialization"), prune_titles=True
        )
        self.reader: EventReader | None = None
        self._hook: Callable[..., Any] | None = None
        self._arguments: Callable[..., dict[str, Any]] = lambda **kwargs: kwargs
        hook = inspect.getattr_static(model, "for_subscription", None)
        if hook is not None:
            if not inspect.isfunction(hook):
                raise TypeError(
                    f"{model.__name__}.for_subscription must be a plain instance method"
                )
            if inspect.iscoroutinefunction(hook):
                raise TypeError(f"{model.__name__}.for_subscription must not be async")
            parameters = _without_self(hook, model)
            self.input_schema = ParsedFunction.from_function(parameters).input_schema
            reason = find_invalid_x_mcp_header(self.input_schema)
            if reason is not None:
                raise ValueError(
                    f"Event {name!r} has an invalid x-mcp-header: {reason}"
                )
            self._arguments = validate_call(parameters)
            self._hook = hook

    def descriptor(self, *, push: bool) -> EventType:
        return EventType(
            name=self.name,
            description=self.description,
            delivery=["poll", "push"] if push else ["poll"],
            input_schema=self.input_schema,
            payload_schema=self.payload_schema,
            meta=self.meta,
        )

    async def emit(
        self,
        payload: BaseModel,
        *,
        event_id: str | None,
        timestamp: datetime | None,
        meta: dict[str, Any] | None,
    ) -> None:
        event = Event(
            data=payload.model_dump(mode="json", by_alias=True),
            # Generated once here, so every poll and stream sees the same ID.
            event_id=event_id or uuid.uuid4().hex,
            timestamp=timestamp or datetime.now(UTC),
            meta=meta,
        )
        await self.log.append(self.name, event)

    async def wait(self, cursor: str | None, timeout: float) -> bool:
        """Wait up to `timeout` seconds for an emit after `cursor`."""
        if self.reader is not None:
            # Nothing is emitted into a reader's type: wait out the interval.
            await anyio.sleep(timeout)
            return False
        return await self.log.wait(self.name, cursor, timeout)

    def arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A subscription's arguments, validated and coerced for the hook."""
        try:
            return self._arguments(**arguments)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid arguments for {self.name}: "
                + (f"{where}: {first['msg']}" if where else first["msg"]),
            ) from None

    def deliver(
        self, event: Event, arguments: dict[str, Any], *, validate: bool
    ) -> Event | None:
        """What a subscription with these validated arguments receives of `event`.

        An emitted event was dumped from the model, so it is only rebuilt when
        there is a hook to run; a reader's event is always checked against it.
        """
        if self._hook is None and not validate:
            return event
        # A fresh instance from a copy of the data on every call: the hook
        # sees the event as it came, and a change it makes, even deep inside
        # a nested value, reaches only this subscription.
        instance = self.model.model_validate(copy.deepcopy(event.data))
        result = instance if self._hook is None else self._hook(instance, **arguments)
        if result is None:
            return None
        if not isinstance(result, self.model):
            raise TypeError(
                f"{self.model.__name__}.for_subscription must return a "
                f"{self.model.__name__} or None, not {type(result).__name__}"
            )
        return replace(event, data=result.model_dump(mode="json", by_alias=True))

    async def poll(self, poll: EventPoll) -> EventBatch:
        # Checked on the first poll too, which reads nothing.
        arguments = self.arguments(poll.arguments)
        if self.reader is not None:
            return await self._from_reader(poll, arguments)
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
                delivered = self.deliver(event, arguments, validate=False)
                if delivered is not None:
                    events.append(delivered)
            cursor = read.cursor
            if not read.more:
                return EventBatch(events=events, cursor=cursor, truncated=truncated)

    async def _from_reader(
        self, poll: EventPoll, arguments: dict[str, Any]
    ) -> EventBatch:
        assert self.reader is not None
        if is_coroutine_function(self.reader):
            batch = self.reader(replace(poll, arguments=arguments))
        else:
            # Like sync tools, a sync reader runs off the event loop so that
            # blocking I/O in it does not stall every other request.
            batch = await call_sync_fn_in_threadpool(
                self.reader, replace(poll, arguments=arguments)
            )
        if inspect.isawaitable(batch):
            batch = await batch
        delivered = [
            self.deliver(event, arguments, validate=True) for event in batch.events
        ]
        return replace(batch, events=[e for e in delivered if e is not None])


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
