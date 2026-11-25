"""How a push subscription recovers from, or ends on, a stream that stops."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from fastmcp_events._constants import NOT_FOUND
from fastmcp_events.client import Frame
from fastmcp_events.models import StreamFrame
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp_types import CONNECTION_CLOSED, JSONRPCNotification
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp_events import (
    Event,
    EventBacklogFullError,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
    subscribe_events,
)
from fastmcp_events import client as events_client_module
from fastmcp_events import subscription as subscription_module

SendRaw = Callable[[str, Any, Any], Awaitable[Any]]


def replaying_server(
    log: list[str], checked: Callable[[str | None], None] | None = None
) -> FastMCP:
    """An upstream that replays its whole log on every check, as some do."""
    server = FastMCP("replays")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)

    @events.event("tick")
    class Tick(BaseModel):
        """A tick"""

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        if checked is not None:
            checked(poll.cursor)
        if poll.cursor is None:
            return EventBatch(cursor="0")
        return EventBatch(
            events=[Event(data={}, event_id=key) for key in log], cursor=str(len(log))
        )

    return server


def events_client(server: FastMCP) -> Client:
    return Client(server, extensions=[EventsClientExtension()])


def intercept_streams(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    handle: Callable[[SendRaw, str, Any, Any], Awaitable[Any]],
) -> None:
    """Route every `events/stream` request through `handle`."""
    dispatcher = client.session._dispatcher
    real: SendRaw = dispatcher.send_raw_request

    async def send_raw_request(method: str, params: Any, options: Any) -> Any:
        if method != "events/stream":
            return await real(method, params, options)
        return await handle(real, method, params, options)

    monkeypatch.setattr(dispatcher, "send_raw_request", send_raw_request)


async def cut_off_once(
    real: SendRaw, method: str, params: Any, options: Any, delivered: anyio.Event
) -> Any:
    """Run a stream until `delivered` is set, then fail it as a dropped stream."""

    async def stream() -> None:
        await real(method, params, options)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(stream)
        await delivered.wait()
        tasks.cancel_scope.cancel()
    raise MCPError(code=CONNECTION_CLOSED, message="SSE stream ended")


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record each reconnect's attempt number and skip the wait."""
    attempts: list[int] = []

    def no_wait(attempt: int) -> float:
        attempts.append(attempt)
        return 0

    monkeypatch.setattr(subscription_module, "_reconnect_delay", no_wait)
    return attempts


async def test_a_dropped_stream_reconnects_from_its_cursor_without_repeats(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    log = ["a", "b"]
    opened_from: list[str | None] = []
    # The first stream's second check runs after it sent its first batch.
    first_batch_sent = anyio.Event()

    def checked(cursor: str | None) -> None:
        if cursor == "2":
            first_batch_sent.set()

    async def drop_the_first(real: SendRaw, method: str, params: Any, options: Any):
        opened_from.append(params.get("cursor"))
        if len(opened_from) > 1:
            return await real(method, params, options)
        try:
            await cut_off_once(real, method, params, options, first_batch_sent)
        finally:
            log.append("c")

    async with events_client(replaying_server(log, checked)) as client:
        intercept_streams(client, monkeypatch, drop_the_first)
        subscription = subscribe_events(client, "tick", cursor="0", delivery="push")
        received: list[str] = []
        with anyio.fail_after(10):
            async for event in subscription:
                received.append(event.event_id)
                if len(received) == 3:
                    break

    assert received == ["a", "b", "c"]
    assert opened_from == ["0", "2"]
    assert delays == [0]


async def test_a_stream_that_keeps_dropping_gives_up_after_a_few_attempts(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    opens = 0

    async def always_drop(real: SendRaw, method: str, params: Any, options: Any):
        nonlocal opens
        opens += 1
        raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

    async with events_client(replaying_server([])) as client:
        intercept_streams(client, monkeypatch, always_drop)
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(10):
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass

    assert exc_info.value.error.code == CONNECTION_CLOSED
    assert delays == [0, 1, 2, 3]
    assert opens == 1 + subscription_module._MAX_RECONNECTS


async def test_a_stream_that_drops_after_making_progress_keeps_reconnecting(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    log: list[str] = []
    batch_sent = anyio.Event()

    def checked(cursor: str | None) -> None:
        # A check from the end of the log follows the batch that reached it.
        if cursor == str(len(log)):
            batch_sent.set()

    async def drop_after_one_event(
        real: SendRaw, method: str, params: Any, options: Any
    ):
        nonlocal batch_sent
        batch_sent = anyio.Event()
        log.append(f"e{len(log)}")
        await cut_off_once(real, method, params, options, batch_sent)

    wanted = subscription_module._MAX_RECONNECTS + 3
    async with events_client(replaying_server(log, checked)) as client:
        intercept_streams(client, monkeypatch, drop_after_one_event)
        subscription = subscribe_events(client, "tick", cursor="0", delivery="push")
        received: list[str] = []
        with anyio.fail_after(10):
            async for event in subscription:
                received.append(event.event_id)
                if len(received) == wanted:
                    break

    assert received == [f"e{n}" for n in range(wanted)]
    assert set(delays) == {0}


async def test_an_error_response_to_a_reconnect_ends_the_subscription(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    server = FastMCP("removed")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)
    opens = 0
    first_check = anyio.Event()

    @events.event("tick")
    class Tick(BaseModel):
        """A tick"""

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        if opens > 1:
            raise MCPError(code=NOT_FOUND, message="Unknown event: tick")
        first_check.set()
        return EventBatch(cursor="0")

    async def drop_the_first(real: SendRaw, method: str, params: Any, options: Any):
        nonlocal opens
        opens += 1
        if opens > 1:
            return await real(method, params, options)
        await cut_off_once(real, method, params, options, first_check)

    async with events_client(server) as client:
        intercept_streams(client, monkeypatch, drop_the_first)
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(10):
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass

    assert exc_info.value.error.code == NOT_FOUND
    assert opens == 2


async def test_an_overflowing_stream_is_stopped_before_the_consumer_drains_it(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    monkeypatch.setattr(events_client_module, "_MAX_PENDING_FRAMES", 4)
    server = FastMCP("firehose")
    events = EventsExtension(poll_interval_ms=0)
    server.add_extension(events)
    checks = 0
    checks_at_overflow: list[int] = []

    @events.event("tick")
    class Tick(BaseModel):
        """A tick"""

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        nonlocal checks
        checks += 1
        position = int(poll.cursor or 0)
        return EventBatch(
            events=[Event(data={}, event_id=str(position + n)) for n in range(10)],
            cursor=str(position + 10),
            has_more=True,
        )

    real_route = EventsClientExtension._route

    def route(self: EventsClientExtension, kind: str, frame: StreamFrame) -> None:
        real_route(self, kind, frame)
        if self._overflowed and not checks_at_overflow:
            checks_at_overflow.append(checks)

    monkeypatch.setattr(EventsClientExtension, "_route", route)

    async with events_client(server) as client:
        with pytest.raises(EventBacklogFullError), anyio.fail_after(10):
            async for _ in subscribe_events(
                client, "tick", cursor="0", delivery="push"
            ):
                await anyio.sleep(0.05)

    # Draining the buffer took the consumer about 0.2 s, in which an unstopped
    # stream runs hundreds of checks; a stopped one finishes the one in hand.
    assert checks - checks_at_overflow[0] <= 3
    assert delays == []


async def test_a_mid_stream_gap_keeps_the_cursor_before_its_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FastMCP("gappy")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)

    @events.event("tick")
    class Tick(BaseModel):
        """A tick"""

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        if poll.cursor == "1":
            return EventBatch(
                events=[Event(data={}, event_id="x"), Event(data={}, event_id="y")],
                cursor="9",
                truncated=True,
            )
        return EventBatch(cursor="1")

    frames: list[tuple[str, str | None]] = []
    real_route = EventsClientExtension._route

    def route(self: EventsClientExtension, kind: str, frame: StreamFrame) -> None:
        frames.append((kind, frame.cursor))
        real_route(self, kind, frame)

    monkeypatch.setattr(EventsClientExtension, "_route", route)

    async with events_client(server) as client:
        subscription = subscribe_events(client, "tick", cursor="0", delivery="push")
        with anyio.fail_after(10):
            async for event in subscription:
                if event.event_id == "y":
                    break

    assert frames[:4] == [
        ("active", "1"),
        ("active", "1"),
        ("event", "1"),
        ("event", "9"),
    ]
    assert subscription.truncated is True


async def test_a_stream_that_never_answers_is_retried_a_bounded_number_of_times(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    monkeypatch.setattr(subscription_module, "_ACTIVATION_TIMEOUT", 0.05)
    server = FastMCP("hung")
    events = EventsExtension()
    server.add_extension(events)
    opens = 0

    @events.event("tick")
    class Tick(BaseModel):
        """A tick"""

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        nonlocal opens
        opens += 1
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    async with events_client(server) as client:
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(10):
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass

    assert exc_info.value.error.code == CONNECTION_CLOSED
    assert opens == 1 + subscription_module._MAX_RECONNECTS


async def test_cancelling_a_subscription_while_it_waits_to_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subscription_module, "_reconnect_delay", lambda attempt: 30)
    extension = EventsClientExtension()

    async def always_drop(real: SendRaw, method: str, params: Any, options: Any):
        raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

    async with Client(replaying_server([]), extensions=[extension]) as client:
        intercept_streams(client, monkeypatch, always_drop)
        with anyio.fail_after(5), anyio.move_on_after(0.2) as scope:
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass

    assert scope.cancelled_caught
    assert extension._streams == {}
    assert extension._stoppers == {}


async def test_a_closed_session_is_not_reconnected(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    opens = 0

    async with events_client(replaying_server([])) as client:
        dispatcher = client.session._dispatcher

        async def session_closes(real: SendRaw, method: str, params: Any, options: Any):
            nonlocal opens
            opens += 1
            monkeypatch.setattr(dispatcher, "_closed", True)
            raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")

        intercept_streams(client, monkeypatch, session_closes)
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(5):
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass
        monkeypatch.setattr(dispatcher, "_closed", False)

    assert exc_info.value.error.code == CONNECTION_CLOSED
    assert opens == 1
    assert delays == []


async def test_frames_reach_the_stream_in_the_order_they_were_received(
    monkeypatch: pytest.MonkeyPatch, delays: list[int]
) -> None:
    """The SDK queues each notification method separately, so without wire-order
    routing a heartbeat overtakes the events read just before it, and its cursor
    skips them if the stream drops before they arrive."""
    log = ["a", "b", "c"]
    opened_from: list[str | None] = []
    arrived: list[list[str]] = []
    real_open = EventsClientExtension._open

    def recording_open(
        self: EventsClientExtension, subscription_id: Any, stop: Callable[[], None]
    ) -> Any:
        frames = real_open(self, subscription_id, stop)
        kinds: list[str] = []
        arrived.append(kinds)
        receive = frames.receive

        async def receive_and_record() -> Frame:
            kind, frame = await receive()
            kinds.append(kind)
            return kind, frame

        frames.receive = receive_and_record  # type: ignore[method-assign]
        return frames

    monkeypatch.setattr(EventsClientExtension, "_open", recording_open)

    async with events_client(replaying_server(log)) as client:
        session = client.session
        dispatcher = session._dispatcher

        async def burst_then_drop(
            real: SendRaw, method: str, params: Any, options: Any
        ):
            opened_from.append(params.get("cursor"))
            if len(opened_from) > 1:
                return await real(method, params, options)
            meta = {SUBSCRIPTION_ID_META_KEY: options["request_id"]}
            event = {"name": "tick", "timestamp": "2026-11-23T00:00:00Z", "data": {}}
            wire = [
                ("active", {"cursor": "0", "heartbeatMs": 30_000}),
                ("event", {**event, "eventId": "a", "cursor": "0"}),
                ("event", {**event, "eventId": "b", "cursor": "0"}),
                ("event", {**event, "eventId": "c", "cursor": "3"}),
                ("heartbeat", {"cursor": "3"}),
            ]
            # One read off the wire, handed to the dispatcher's receive path.
            for kind, frame in wire:
                message = JSONRPCNotification(
                    jsonrpc="2.0",
                    method=f"notifications/events/{kind}",
                    params={**frame, "_meta": meta},
                )
                await dispatcher._dispatch(  # type: ignore[attr-defined]
                    SessionMessage(message), None, session._on_notify, None
                )
            while len(arrived[0]) < len(wire):
                await anyio.sleep(0.01)
            log.append("d")
            raise MCPError(code=CONNECTION_CLOSED, message="SSE stream ended")

        intercept_streams(client, monkeypatch, burst_then_drop)
        subscription = subscribe_events(client, "tick", cursor="0", delivery="push")
        received: list[str] = []
        with anyio.fail_after(10):
            async for event in subscription:
                received.append(event.event_id)
                if len(received) == 4:
                    break

    assert arrived[0] == ["active", "event", "event", "event", "heartbeat"]
    assert received == ["a", "b", "c", "d"]
    assert opened_from == ["0", "3"]
