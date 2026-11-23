"""Event types declared as a payload model and fed by `EventsExtension.emit`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp import Client, FastMCP
from fastmcp.experimental.events import (
    EventPayload,
    EventsClientExtension,
    EventsExtension,
    list_events,
    poll_events_mcp,
    subscribe_events,
)


class Severity(Enum):
    LOW = "low"
    HIGH = "high"


class AlertFired(EventPayload, name="alert.fired"):
    """An alert fired."""

    project: str
    severity: Severity = Severity.LOW

    def matches(self, project: str | None = None, severity: Severity | None = None):
        return (project is None or self.project == project) and (
            severity is None or self.severity == severity
        )


def make_server(**extension_kwargs) -> tuple[FastMCP, EventsExtension]:
    server = FastMCP("alerts")
    events = EventsExtension(events=[AlertFired], **extension_kwargs)
    server.add_extension(events)
    return server, events


def client(server: FastMCP) -> Client:
    return Client(server, extensions=[EventsClientExtension()])


async def test_a_model_declares_the_event_type():
    server, _ = make_server()
    async with client(server) as c:
        [event_type] = await list_events(c)
    assert event_type.name == "alert.fired"
    assert event_type.description == "An alert fired."
    assert event_type.payload_schema is not None
    assert set(event_type.payload_schema["properties"]) == {"project", "severity"}
    assert event_type.input_schema is not None
    assert set(event_type.input_schema["properties"]) == {"project", "severity"}
    assert "required" not in event_type.input_schema


async def test_poll_returns_emitted_events_that_match_the_subscription():
    server, events = make_server()
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired", {"project": "api"})
        assert start.events == []
        await events.emit(AlertFired(project="api"), event_id="a1")
        await events.emit(AlertFired(project="web"), event_id="w1")
        await events.emit(
            AlertFired(project="api", severity=Severity.HIGH), event_id="a2"
        )

        api = await poll_events_mcp(
            c, "alert.fired", {"project": "api"}, cursor=start.cursor
        )
        high = await poll_events_mcp(
            c, "alert.fired", {"severity": "high"}, cursor=start.cursor
        )
        again = await poll_events_mcp(c, "alert.fired", {}, cursor=api.cursor)

    assert [e.event_id for e in api.events] == ["a1", "a2"]
    assert api.events[1].data == {"project": "api", "severity": "high"}
    assert [e.event_id for e in high.events] == ["a2"]
    assert again.events == []
    assert not api.truncated and not api.has_more


async def test_a_generated_event_id_is_the_same_on_every_poll():
    server, events = make_server()
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired")
        await events.emit(AlertFired(project="api"))
        first = await poll_events_mcp(c, "alert.fired", cursor=start.cursor)
        second = await poll_events_mcp(c, "alert.fired", cursor=start.cursor)
    assert first.events[0].event_id == second.events[0].event_id


async def test_max_events_pages_through_the_buffer():
    server, events = make_server()
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired")
        for n in range(5):
            await events.emit(AlertFired(project="api"), event_id=str(n))
        page = await poll_events_mcp(
            c, "alert.fired", cursor=start.cursor, max_events=2
        )
        rest = await poll_events_mcp(c, "alert.fired", cursor=page.cursor)
    assert [e.event_id for e in page.events] == ["0", "1"]
    assert page.has_more
    assert [e.event_id for e in rest.events] == ["2", "3", "4"]
    assert not rest.has_more


async def test_a_cursor_older_than_the_buffer_is_a_gap():
    server, events = make_server(emit_buffer=2)
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired")
        for n in range(3):
            await events.emit(AlertFired(project="api"), event_id=str(n))
        result = await poll_events_mcp(c, "alert.fired", cursor=start.cursor)
    assert result.truncated
    assert [e.event_id for e in result.events] == ["1", "2"]


async def test_a_cursor_from_another_process_replays_the_buffer_as_a_gap():
    server, events = make_server()
    async with client(server) as c:
        await events.emit(AlertFired(project="api"), event_id="since-restart")
        result = await poll_events_mcp(c, "alert.fired", cursor="0123456789ab-41")
    assert result.truncated
    assert [e.event_id for e in result.events] == ["since-restart"]


async def test_max_age_skips_older_events_as_a_gap():
    server, events = make_server()
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired")
        await events.emit(AlertFired(project="api"), event_id="old", timestamp=long_ago)
        await events.emit(AlertFired(project="api"), event_id="new")
        result = await poll_events_mcp(
            c, "alert.fired", cursor=start.cursor, max_age_ms=60_000
        )
    assert result.truncated
    assert [e.event_id for e in result.events] == ["new"]


async def test_arguments_that_do_not_fit_matches_are_rejected():
    server, _ = make_server()
    async with client(server) as c:
        with pytest.raises(MCPError) as caught:
            await poll_events_mcp(c, "alert.fired", {"severity": "medium"})
    assert caught.value.error.code == INVALID_PARAMS


async def test_push_delivers_an_emit_without_waiting_for_the_poll_interval():
    server, events = make_server(poll_interval_ms=60_000)
    received = []
    async with client(server) as c:
        subscription = subscribe_events(
            c, "alert.fired", {"project": "api"}, delivery="push"
        )

        async def consume():
            async for event in subscription:
                received.append(event.event_id)
                if len(received) == 2:
                    return

        with anyio.fail_after(3):
            async with anyio.create_task_group() as tg:
                tg.start_soon(consume)
                while subscription.cursor is None:
                    await anyio.sleep(0.01)
                await events.emit(AlertFired(project="web"), event_id="w1")
                await events.emit(AlertFired(project="api"), event_id="a1")
                await events.emit(AlertFired(project="api"), event_id="a2")
    assert received == ["a1", "a2"]


async def test_a_model_without_matches_takes_no_arguments_and_gets_everything():
    class DeployFinished(EventPayload, name="deploy.finished"):
        service: str

    server = FastMCP("deploys")
    events = EventsExtension(events=[DeployFinished])
    server.add_extension(events)

    async with client(server) as c:
        [event_type] = await list_events(c)
        start = await poll_events_mcp(c, "deploy.finished")
        await events.emit(DeployFinished(service="api"), event_id="d1")
        result = await poll_events_mcp(c, "deploy.finished", cursor=start.cursor)
    assert event_type.input_schema is None
    assert event_type.description is None
    assert [e.event_id for e in result.events] == ["d1"]


def test_emitting_a_type_the_server_does_not_serve_is_an_error():
    _, events = make_server()

    class Other(EventPayload, name="other.event"):
        x: int

    with pytest.raises(ValueError, match="not an event type this server emits"):
        anyio.run(lambda: events.emit(Other(x=1)))


def test_two_types_with_one_name_are_an_error():
    class Again(EventPayload, name="alert.fired"):
        x: int

    with pytest.raises(ValueError, match="already registered"):
        EventsExtension(events=[AlertFired, Again])


def test_a_type_without_a_name_is_not_an_event():
    class Base(EventPayload):
        project: str

    class Named(Base, name="named.event"):
        pass

    assert Base.__event_name__ is None
    assert Named.__event_name__ == "named.event"
    with pytest.raises(TypeError, match="declares no event type"):
        EventsExtension(events=[Base])


def test_the_decorator_no_longer_takes_a_model():
    events = EventsExtension()
    with pytest.raises(TypeError, match="subclassing EventPayload"):
        events.event("alert.again")(AlertFired)  # ty: ignore[invalid-argument-type]


def test_matches_must_be_a_sync_instance_method():
    with pytest.raises(TypeError, match="must not be async"):

        class Async(EventPayload, name="async.event"):
            x: int

            async def matches(self, x: int | None = None) -> bool:
                return True
