from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import HEADER_MISMATCH, INVALID_PARAMS, METHOD_NOT_FOUND
from starlette.testclient import TestClient

from fastmcp import Client, FastMCP
from fastmcp.events import (
    Event,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
)
from fastmcp.events._constants import EVENTS_EXTENSION_ID, NOT_FOUND
from fastmcp.events.models import (
    PollEventsParams,
    PollEventsRequest,
    PollEventsResult,
)

FIRED_AT = datetime(2026, 11, 14, 2, 17, 30, tzinfo=UTC)


def make_server(log: list[dict] | None = None, **extension_kwargs) -> FastMCP:
    """A server with one event type backed by an append-only log.

    The cursor is the number of log entries the client has seen.
    """
    log = log if log is not None else []
    server = FastMCP("alerts")
    events = EventsExtension(**extension_kwargs)
    server.add_extension(events)

    @events.event(
        name="alert.fired",
        description="An alert fired",
        input_schema={
            "type": "object",
            "properties": {"severity": {"type": "string"}},
            "additionalProperties": False,
        },
        payload_schema={"type": "object", "properties": {"key": {"type": "string"}}},
    )
    async def alert_fired(poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            return EventBatch(cursor=str(len(log)))
        start = int(poll.cursor)
        severity = poll.arguments.get("severity")
        end = len(log) if poll.max_events is None else start + poll.max_events
        return EventBatch(
            events=[
                Event(data=entry, event_id=entry["key"], timestamp=FIRED_AT)
                for entry in log[start:end]
                if severity is None or entry["severity"] == severity
            ],
            cursor=str(min(end, len(log))),
            has_more=end < len(log),
        )

    return server


def events_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[EventsClientExtension()])


async def test_advertises_events_capability() -> None:
    async with events_client(make_server()) as client:
        assert client.server_capabilities is not None
        assert client.server_capabilities.extensions is not None
        assert client.server_capabilities.extensions[EVENTS_EXTENSION_ID] == {}


async def test_list_events_describes_poll_delivery_and_schemas() -> None:
    async with events_client(make_server()) as client:
        (event,) = await client.list_events()

    assert event.name == "alert.fired"
    assert event.description == "An alert fired"
    assert event.delivery == ["poll"]
    assert event.input_schema is not None
    assert event.input_schema["properties"] == {"severity": {"type": "string"}}
    assert event.payload_schema is not None


async def test_list_events_paginates() -> None:
    server = FastMCP("many", list_page_size=2)
    events = EventsExtension()
    server.add_extension(events)
    for name in ("c", "a", "b"):
        events.event(name=name)(lambda poll: EventBatch())

    async with events_client(server) as client:
        first = await client.list_events_mcp()
        listed = await client.list_events()

    assert [event.name for event in first.events] == ["a", "b"]
    assert first.next_cursor is not None
    assert [event.name for event in listed] == ["a", "b", "c"]


async def test_first_poll_starts_from_now() -> None:
    log = [{"key": "old", "severity": "high"}]

    async with events_client(make_server(log)) as client:
        result = await client.poll_events_mcp("alert.fired")

    assert result.events == []
    assert result.cursor == "1"
    assert result.truncated is False
    assert result.has_more is False
    assert result.next_poll_ms == 30_000


async def test_poll_returns_events_after_the_cursor() -> None:
    log = [{"key": "old", "severity": "high"}]

    async with events_client(make_server(log, poll_interval_ms=5_000)) as client:
        start = await client.poll_events_mcp("alert.fired")
        log.append({"key": "disk-full", "severity": "high"})
        log.append({"key": "slow-query", "severity": "low"})
        result = await client.poll_events_mcp(
            "alert.fired", {"severity": "high"}, cursor=start.cursor
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
        first = await client.poll_events_mcp("alert.fired", cursor="0", max_events=2)
        rest = await client.poll_events_mcp(
            "alert.fired", cursor=first.cursor, max_events=2
        )

    assert [event.event_id for event in first.events] == ["0", "1"]
    assert first.has_more is True
    assert [event.event_id for event in rest.events] == ["2"]
    assert rest.has_more is False


async def test_events_without_an_id_or_timestamp_get_generated_ones() -> None:
    server = FastMCP("bare")
    events = EventsExtension()
    server.add_extension(events)

    @events.event(name="tick")
    def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(events=[Event(data={}), Event(data={})])

    async with events_client(server) as client:
        result = await client.poll_events_mcp("tick")

    first, second = result.events
    assert first.event_id and second.event_id and first.event_id != second.event_id
    assert datetime.fromisoformat(first.timestamp).tzinfo is not None
    assert result.cursor is None


async def test_unknown_event_is_not_found() -> None:
    async with events_client(make_server()) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.poll_events_mcp("missing")

    assert exc_info.value.error.code == NOT_FOUND
    assert exc_info.value.error.data == {"kind": "event"}


async def test_arguments_outside_the_input_schema_are_invalid_params() -> None:
    async with events_client(make_server()) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.poll_events_mcp("alert.fired", {"severity": 3})

    assert exc_info.value.error.code == INVALID_PARAMS


async def test_subscription_drains_backlog_resumes_and_drops_repeats() -> None:
    log = [{"key": str(n), "severity": "low"} for n in range(3)]
    server = make_server(log, poll_interval_ms=0)

    async with events_client(server) as client:
        subscription = client.subscribe_events(
            "alert.fired", cursor="0", max_events=2, min_poll_interval=0
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


async def test_subscription_reports_a_gap() -> None:
    server = FastMCP("gap")
    events = EventsExtension(poll_interval_ms=0)
    server.add_extension(events)

    @events.event(name="tick")
    def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(
            events=[Event(data={}, event_id="after-gap")],
            cursor="fresh",
            truncated=True,
        )

    async with events_client(server) as client:
        subscription = client.subscribe_events(
            "tick", cursor="expired", min_poll_interval=0
        )
        async for _ in subscription:
            break

    assert subscription.truncated is True
    assert subscription.cursor == "fresh"


async def test_duplicate_event_name_is_rejected() -> None:
    events = EventsExtension()
    events.event(name="tick")(lambda poll: EventBatch())

    with pytest.raises(ValueError, match="already registered"):
        events.event(name="tick")


async def test_client_requires_explicit_opt_in() -> None:
    async with Client(make_server(), mode="auto") as client:
        with pytest.raises(RuntimeError, match="did not opt into"):
            await client.list_events()


async def test_client_rejects_server_without_extension() -> None:
    async with events_client(FastMCP("plain")) as client:
        with pytest.raises(RuntimeError, match="does not advertise"):
            await client.poll_events_mcp("alert.fired")


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


def post_poll(http: TestClient, name: str, mcp_name: str | None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "events/poll",
    }
    if mcp_name is not None:
        headers["Mcp-Name"] = mcp_name
    return http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "events/poll",
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
