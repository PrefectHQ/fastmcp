from __future__ import annotations

import threading
from datetime import UTC, datetime

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import HEADER_MISMATCH, INVALID_PARAMS, METHOD_NOT_FOUND
from pydantic import BaseModel
from starlette.testclient import TestClient

from fastmcp import Client, FastMCP
from fastmcp.experimental.events import (
    Event,
    EventBacklogFullError,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
    EventSubscription,
    list_events,
    list_events_mcp,
    poll_events_mcp,
    subscribe_events,
)
from fastmcp.experimental.events import client as events_client_module
from fastmcp.experimental.events._constants import (
    EVENTS_EXTENSION_ID,
    FORBIDDEN,
    NOT_FOUND,
    UNSUPPORTED,
)
from fastmcp.experimental.events.models import (
    EventOccurrence,
    PollEventsParams,
    PollEventsRequest,
    PollEventsResult,
)
from fastmcp.experimental.events.subscription import Delivery
from fastmcp.utilities.tests import run_server_async

FIRED_AT = datetime(2026, 11, 14, 2, 17, 30, tzinfo=UTC)


class AlertFired(BaseModel):
    """An alert fired"""

    key: str
    severity: str

    def for_subscription(self, severity: str | None = None) -> AlertFired | None:
        return self if severity is None or self.severity == severity else None


class Tick(BaseModel):
    """A tick"""


def make_server(log: list[dict] | None = None, **extension_kwargs) -> FastMCP:
    """A server with one event type read from an append-only log.

    The cursor is the number of log entries the client has seen.
    """
    log = log if log is not None else []
    server = FastMCP("alerts")
    events = EventsExtension(**extension_kwargs)
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)

    @events.reader(AlertFired)
    async def read_alerts(poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=str(len(log)))
        start = int(poll.cursor)
        end = len(log) if poll.max_events is None else start + poll.max_events
        return EventBatch(
            events=[
                Event(data=entry, event_id=entry["key"], timestamp=FIRED_AT)
                for entry in log[start:end]
            ],
            cursor=str(min(end, len(log))),
            has_more=end < len(log),
        )

    return server


def tick_server(**extension_kwargs) -> tuple[FastMCP, EventsExtension]:
    """A server with a `tick` event type, for a test to give a reader."""
    server = FastMCP("ticks")
    events = EventsExtension(**extension_kwargs)
    server.add_extension(events)
    events.event("tick")(Tick)
    return server, events


def events_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[EventsClientExtension()])


async def test_advertises_events_capability() -> None:
    async with events_client(make_server()) as client:
        assert client.server_capabilities is not None
        assert client.server_capabilities.extensions is not None
        assert client.server_capabilities.extensions[EVENTS_EXTENSION_ID] == {}


async def test_list_events_describes_delivery_and_schemas() -> None:
    async with events_client(make_server()) as client:
        (event,) = await list_events(client)

    assert event.name == "alert.fired"
    assert event.description == "An alert fired"
    assert event.delivery == ["poll", "push"]
    assert event.input_schema is not None
    assert set(event.input_schema["properties"]) == {"severity"}
    assert event.input_schema["additionalProperties"] is False
    assert event.payload_schema is not None
    assert set(event.payload_schema["properties"]) == {"key", "severity"}


async def test_list_events_paginates() -> None:
    server = FastMCP("many", list_page_size=2)
    events = EventsExtension()
    server.add_extension(events)
    for name in ("c", "a", "b"):
        events.event(name)(type(name.upper(), (BaseModel,), {}))

    async with events_client(server) as client:
        first = await list_events_mcp(client)
        listed = await list_events(client)

    assert [event.name for event in first.events] == ["a", "b"]
    assert first.next_cursor is not None
    assert [event.name for event in listed] == ["a", "b", "c"]


async def test_first_poll_starts_from_now() -> None:
    log = [{"key": "old", "severity": "high"}]

    async with events_client(make_server(log)) as client:
        result = await poll_events_mcp(client, "alert.fired")

    assert result.events == []
    assert result.cursor == "1"
    assert result.truncated is False
    assert result.has_more is False
    assert result.next_poll_ms == 30_000


async def test_poll_returns_events_after_the_cursor() -> None:
    log = [{"key": "old", "severity": "high"}]

    async with events_client(make_server(log, poll_interval_ms=5_000)) as client:
        start = await poll_events_mcp(client, "alert.fired")
        log.append({"key": "disk-full", "severity": "high"})
        log.append({"key": "slow-query", "severity": "low"})
        result = await poll_events_mcp(
            client, "alert.fired", {"severity": "high"}, cursor=start.cursor
        )

    (event,) = result.events
    assert event.event_id == "disk-full"
    assert event.name == "alert.fired"
    assert event.timestamp == "2026-11-14T02:17:30+00:00"
    assert event.data == {"key": "disk-full", "severity": "high"}
    assert result.cursor == "3"
    assert result.next_poll_ms == 5_000


async def test_max_events_leaves_a_backlog_to_drain() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]

    async with events_client(make_server(log)) as client:
        first = await poll_events_mcp(client, "alert.fired", cursor="0", max_events=2)
        rest = await poll_events_mcp(
            client, "alert.fired", cursor=first.cursor, max_events=2
        )

    assert [event.event_id for event in first.events] == ["0", "1"]
    assert first.has_more is True
    assert [event.event_id for event in rest.events] == ["2"]
    assert rest.has_more is False


async def test_events_without_an_id_or_timestamp_get_generated_ones() -> None:
    server, events = tick_server()

    @events.reader(Tick)
    def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(events=[Event(data={}), Event(data={})])

    async with events_client(server) as client:
        result = await poll_events_mcp(client, "tick")

    first, second = result.events
    assert first.event_id and second.event_id and first.event_id != second.event_id
    assert datetime.fromisoformat(first.timestamp).tzinfo is not None
    assert result.cursor is None


async def test_unknown_event_is_not_found() -> None:
    async with events_client(make_server()) as client:
        with pytest.raises(MCPError) as exc_info:
            await poll_events_mcp(client, "missing")

    assert exc_info.value.error.code == NOT_FOUND
    assert exc_info.value.error.data == {"kind": "event"}


async def test_arguments_outside_the_input_schema_are_invalid_params() -> None:
    async with events_client(make_server()) as client:
        with pytest.raises(MCPError) as exc_info:
            await poll_events_mcp(client, "alert.fired", {"severity": 3})

    assert exc_info.value.error.code == INVALID_PARAMS


async def test_subscription_drains_backlog_resumes_and_drops_repeats() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]
    server = make_server(log, poll_interval_ms=0)

    async with events_client(server) as client:
        subscription = subscribe_events(
            client,
            "alert.fired",
            cursor="0",
            max_events=2,
            min_poll_interval=0,
            delivery="poll",
        )
        seen = []
        async for event in subscription:
            seen.append(event.event_id)
            if len(seen) == 3:
                log.append({"key": "1", "severity": "low"})
                log.append({"key": "3", "severity": "low"})
            if len(seen) == 4:
                break

    assert seen == ["0", "1", "2", "3"]
    assert subscription.cursor == "5"
    assert subscription.truncated is False


async def test_stopping_partway_through_a_batch_keeps_the_rest_resumable() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]
    server = make_server(log, poll_interval_ms=0)

    async with events_client(server) as client:
        subscription = subscribe_events(
            client, "alert.fired", cursor="0", min_poll_interval=0, delivery="poll"
        )
        async for _ in subscription:
            break
        assert subscription.cursor == "0"

        resumed = subscribe_events(
            client,
            "alert.fired",
            cursor=subscription.cursor,
            min_poll_interval=0,
            delivery="poll",
        )
        seen = []
        async for event in resumed:
            seen.append(event.event_id)
            if len(seen) == 3:
                break

    assert seen == ["0", "1", "2"]
    assert resumed.cursor == "3"


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_iterating_again_after_stopping_partway_yields_the_rest(
    delivery: Delivery,
) -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]
    server = make_server(log, poll_interval_ms=10)

    async with events_client(server) as client:
        subscription = subscribe_events(
            client, "alert.fired", cursor="0", min_poll_interval=0, delivery=delivery
        )
        with anyio.fail_after(10):
            first = await collect(subscription, 1)
            rest = await collect(subscription, 2)

    assert [event.event_id for event in first + rest] == ["0", "1", "2"]
    assert subscription.cursor == "3"


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_an_events_own_meta_reaches_the_client(delivery: Delivery) -> None:
    server, events = tick_server(poll_interval_ms=10)

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        if poll.cursor == "0":
            return EventBatch(
                events=[Event(data={}, event_id="a", meta={"source": "upstream"})],
                cursor="1",
            )
        return EventBatch(cursor="1")

    async with events_client(server) as client:
        subscription = subscribe_events(client, "tick", cursor="0", delivery=delivery)
        with anyio.fail_after(10):
            (event,) = await collect(subscription, 1)

    assert event.meta == {"source": "upstream"}


async def test_subscription_reports_a_gap() -> None:
    server, events = tick_server(poll_interval_ms=0)

    @events.reader(Tick)
    def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(
            events=[Event(data={}, event_id="after-gap")],
            cursor="fresh",
            truncated=True,
        )

    async with events_client(server) as client:
        subscription = subscribe_events(
            client, "tick", cursor="expired", min_poll_interval=0, delivery="poll"
        )
        async for _ in subscription:
            break

    assert subscription.truncated is True
    assert subscription.cursor == "fresh"


async def collect(subscription, count: int) -> list[EventOccurrence]:
    events = []
    async for event in subscription:
        events.append(event)
        if len(events) == count:
            break
    return events


async def test_push_delivers_events_as_the_log_grows() -> None:
    log = [{"key": "old", "severity": "low"}]
    server = make_server(log, poll_interval_ms=10)

    async with events_client(server) as client:
        subscription = subscribe_events(client, "alert.fired", {"severity": "high"})

        async def grow() -> None:
            await anyio.sleep(0.05)
            log.append({"key": "slow-query", "severity": "low"})
            log.append({"key": "disk-full", "severity": "high"})
            await anyio.sleep(0.05)
            log.append({"key": "oom", "severity": "high"})

        events: list[EventOccurrence] = []
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(grow)
            with anyio.fail_after(10):
                events = await collect(subscription, 2)

    assert [event.event_id for event in events] == ["disk-full", "oom"]
    assert events[0].name == "alert.fired"
    assert events[0].data == {"key": "disk-full", "severity": "high"}
    assert subscription.cursor == "4"


async def test_push_resumes_from_a_cursor_and_drains_the_backlog() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]

    async with events_client(make_server(log, poll_interval_ms=10)) as client:
        subscription = subscribe_events(client, "alert.fired", cursor="1")
        with anyio.fail_after(10):
            events = await collect(subscription, 2)

    assert [event.event_id for event in events] == ["1", "2"]
    assert subscription.cursor == "3"


async def test_stopping_partway_through_a_pushed_backlog_keeps_the_rest() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]

    async with events_client(make_server(log, poll_interval_ms=10)) as client:
        subscription = subscribe_events(
            client, "alert.fired", cursor="0", delivery="push"
        )
        with anyio.fail_after(10):
            (first,) = await collect(subscription, 1)

    assert first.event_id == "0"
    assert subscription.cursor == "0"


async def test_push_reports_a_gap_and_keeps_the_stream_through_a_failed_read() -> None:
    server, events = tick_server(poll_interval_ms=10)
    reads = 0

    @events.reader(Tick)
    def tick(poll: EventPoll) -> EventBatch:
        nonlocal reads
        reads += 1
        if reads == 1:
            return EventBatch(cursor="fresh", truncated=True)
        if reads == 2:
            raise RuntimeError("upstream is down")
        return EventBatch(events=[Event(data={}, event_id="recovered")], cursor="next")

    async with events_client(server) as client:
        subscription = subscribe_events(client, "tick", cursor="expired")
        with anyio.fail_after(10):
            (event,) = await collect(subscription, 1)

    assert event.event_id == "recovered"
    assert subscription.truncated is True
    assert subscription.cursor == "next"


async def test_push_terminates_when_a_reader_reports_access_revoked() -> None:
    server = FastMCP("channels")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)
    reads = 0

    @events.event("message.posted")
    class MessagePosted(BaseModel):
        """A message was posted"""

    @events.reader(MessagePosted)
    def message_posted(poll: EventPoll) -> EventBatch:
        nonlocal reads
        reads += 1
        if reads > 1:
            raise MCPError(code=FORBIDDEN, message="Forbidden")
        return EventBatch(cursor="0")

    async with events_client(server) as client:
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(5):
            async for _ in subscribe_events(client, "message.posted", delivery="push"):
                pass

    assert exc_info.value.error.code == FORBIDDEN
    assert reads == 2


async def test_a_sync_reader_runs_off_the_event_loop() -> None:
    server, events = tick_server()
    reader_threads: list[int] = []

    @events.reader(Tick)
    def tick(poll: EventPoll) -> EventBatch:
        reader_threads.append(threading.get_ident())
        return EventBatch(cursor="0")

    async with events_client(server) as client:
        await poll_events_mcp(client, "tick")

    assert reader_threads and reader_threads[0] != threading.get_ident()


async def test_a_push_stream_with_no_interval_still_lets_other_tasks_run() -> None:
    server, events = tick_server(poll_interval_ms=0)

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(cursor="0")

    async with events_client(server) as client:
        with anyio.move_on_after(0.2) as scope:
            async for _ in subscribe_events(client, "tick", delivery="push"):
                pass

    assert scope.cancelled_caught


async def test_a_push_consumer_that_falls_behind_ends_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_client_module, "_MAX_PENDING_FRAMES", 4)
    log = [{"key": str(n), "severity": "low"} for n in range(20)]

    received: list[str] = []
    async with events_client(make_server(log, poll_interval_ms=10)) as client:
        subscription = subscribe_events(
            client, "alert.fired", cursor="0", delivery="push"
        )
        with pytest.raises(EventBacklogFullError), anyio.fail_after(10):
            async for event in subscription:
                received.append(event.event_id)
                await anyio.sleep(0.01)

    # One batch overflowed before its last event, so the cursor still points
    # at the batch's start: resuming repeats events instead of skipping them.
    assert received == [entry["key"] for entry in log][: len(received)]
    assert len(received) < len(log)
    assert subscription.cursor == "0"


async def test_a_terminated_push_subscription_does_not_fall_back_to_poll() -> None:
    server, events = tick_server(poll_interval_ms=10)
    reads: list[str | None] = []

    @events.reader(Tick)
    async def tick(poll: EventPoll) -> EventBatch:
        reads.append(poll.cursor)
        if len(reads) > 1:
            raise MCPError(code=UNSUPPORTED, message="Schema changed")
        return EventBatch(cursor="0")

    async with events_client(server) as client:
        with pytest.raises(MCPError) as exc_info, anyio.fail_after(5):
            async for _ in subscribe_events(client, "tick"):
                pass

    assert exc_info.value.error.code == UNSUPPORTED
    assert reads == [None, "0"]


async def test_push_rejects_an_unknown_event_before_any_stream_opens() -> None:
    async with events_client(make_server()) as client:
        with pytest.raises(MCPError) as exc_info:
            async for _ in subscribe_events(client, "missing", delivery="push"):
                pass

    assert exc_info.value.error.code == NOT_FOUND


async def test_a_server_without_push_is_polled_and_refuses_a_stream() -> None:
    log = [{"key": "0", "severity": "low"}]
    server = make_server(log, poll_interval_ms=0, push=False)

    async with events_client(server) as client:
        (listed,) = await list_events(client)
        polled = await collect(
            subscribe_events(client, "alert.fired", cursor="0", min_poll_interval=0), 1
        )
        with pytest.raises(MCPError) as exc_info:
            async for _ in subscribe_events(client, "alert.fired", delivery="push"):
                pass

    assert listed.delivery == ["poll"]
    assert [event.event_id for event in polled] == ["0"]
    assert exc_info.value.error.code == UNSUPPORTED
    assert exc_info.value.error.data == {"feature": "deliveryMode", "value": "push"}


async def test_push_is_delivered_over_streamable_http() -> None:
    log: list[dict] = []
    server = make_server(log, poll_interval_ms=10)

    async with run_server_async(server) as url:
        async with Client(url, extensions=[EventsClientExtension()]) as client:
            subscription = subscribe_events(client, "alert.fired")

            async def grow() -> None:
                await anyio.sleep(0.2)
                log.append({"key": "disk-full", "severity": "high"})

            events: list[EventOccurrence] = []
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(grow)
                with anyio.fail_after(15):
                    events = await collect(subscription, 1)

    assert [event.event_id for event in events] == ["disk-full"]
    assert subscription.cursor == "1"


def test_a_json_response_app_refuses_a_stream_it_cannot_carry() -> None:
    app = make_server().http_app(stateless_http=True, json_response=True)

    with TestClient(app) as http:
        response = post_poll(http, "alert.fired", "alert.fired", "events/stream")

    assert response.json()["error"]["code"] == UNSUPPORTED


async def test_a_refused_stream_falls_back_to_poll_unless_push_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = [{"key": "0", "severity": "low"}]
    server = make_server(log, poll_interval_ms=0, push=False)

    async def offered_push(self: EventSubscription) -> str:
        return "push"

    monkeypatch.setattr(EventSubscription, "_choose_delivery", offered_push)

    async with events_client(server) as client:
        subscription = subscribe_events(
            client, "alert.fired", cursor="0", min_poll_interval=0
        )
        polled = await collect(subscription, 1)

    assert [event.event_id for event in polled] == ["0"]


async def test_duplicate_event_name_is_rejected() -> None:
    events = EventsExtension()
    events.event("tick")(Tick)

    with pytest.raises(ValueError, match="already registered"):
        events.event("tick")


async def test_client_requires_explicit_opt_in() -> None:
    async with Client(make_server(), mode="auto") as client:
        with pytest.raises(RuntimeError, match="did not opt into"):
            await list_events(client)


async def test_client_rejects_server_without_extension() -> None:
    async with events_client(FastMCP("plain")) as client:
        with pytest.raises(RuntimeError, match="does not advertise"):
            await poll_events_mcp(client, "alert.fired")


async def test_server_without_extension_answers_method_not_found() -> None:
    async with Client(FastMCP("plain"), mode="auto") as client:
        request = PollEventsRequest(params=PollEventsParams(name="alert.fired"))
        with pytest.raises(MCPError) as exc_info:
            await client.session.send_request(request, PollEventsResult)

    assert exc_info.value.error.code == METHOD_NOT_FOUND


async def test_server_rejects_events_method_on_legacy_protocol() -> None:
    async with Client(make_server(), mode="legacy") as client:
        request = PollEventsRequest(params=PollEventsParams(name="alert.fired"))
        with pytest.raises(MCPError) as exc_info:
            await client.session.send_request(request, PollEventsResult)

    assert exc_info.value.error.code == METHOD_NOT_FOUND


def post_poll(
    http: TestClient, name: str, mcp_name: str | None, method: str = "events/poll"
):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if mcp_name is not None:
        headers["Mcp-Name"] = mcp_name
    return http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                "name": name,
                "cursor": "0",
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {
                        "extensions": {EVENTS_EXTENSION_ID: {}}
                    },
                },
            },
        },
        headers=headers,
    )


@pytest.mark.parametrize("mcp_name", [None, "other.event"])
def test_poll_rejects_a_missing_or_mismatched_name_header(mcp_name: str | None) -> None:
    app = make_server().http_app(stateless_http=True, json_response=True)

    with TestClient(app) as http:
        response = post_poll(http, "alert.fired", mcp_name)

    assert response.json()["error"]["code"] == HEADER_MISMATCH


def test_poll_is_served_by_a_stateless_http_app() -> None:
    log = [{"key": "disk-full", "severity": "high"}]
    app = make_server(log).http_app(stateless_http=True, json_response=True)

    with TestClient(app) as http:
        response = post_poll(http, "alert.fired", "alert.fired")

    assert response.status_code == 200, response.text[:300]
    result = response.json()["result"]
    result.pop("_meta")
    assert result == {
        "resultType": "complete",
        "events": [
            {
                "eventId": "disk-full",
                "name": "alert.fired",
                "timestamp": "2026-11-14T02:17:30+00:00",
                "data": {"key": "disk-full", "severity": "high"},
            }
        ],
        "cursor": "1",
        "truncated": False,
        "hasMore": False,
        "nextPollMs": 30000,
    }
