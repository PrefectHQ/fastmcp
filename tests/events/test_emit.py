"""Event types declared as a payload model and fed by `EventsExtension.emit`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import BaseModel, Field

from fastmcp import Client, FastMCP
from fastmcp_events import (
    EventsClientExtension,
    EventsExtension,
    list_events,
    poll_events_mcp,
    subscribe_events,
)


class Severity(Enum):
    LOW = "low"
    HIGH = "high"


class AlertFired(BaseModel):
    """An alert fired."""

    project: str
    severity: Severity = Severity.LOW
    message: str | None = None

    def for_subscription(
        self,
        project: str | None = None,
        severity: Severity | None = None,
        include_message: bool = True,
    ) -> AlertFired | None:
        if project is not None and self.project != project:
            return None
        if severity is not None and self.severity != severity:
            return None
        if not include_message:
            return self.model_copy(update={"message": None})
        return self


def make_server(**extension_kwargs) -> tuple[FastMCP, EventsExtension]:
    server = FastMCP("alerts")
    events = EventsExtension(**extension_kwargs)
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
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
    assert set(event_type.payload_schema["properties"]) == {
        "project",
        "severity",
        "message",
    }
    assert event_type.input_schema is not None
    assert set(event_type.input_schema["properties"]) == {
        "project",
        "severity",
        "include_message",
    }
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
    assert api.events[1].data == {"project": "api", "severity": "high", "message": None}
    assert [e.event_id for e in high.events] == ["a2"]
    assert again.events == []
    assert not api.truncated and not api.has_more


async def test_for_subscription_sees_the_payload_as_emitted():
    server, events = make_server()
    async with client(server) as c:
        payments = await poll_events_mcp(c, "alert.fired", {"project": "payments"})
        search = await poll_events_mcp(c, "alert.fired", {"project": "search"})
        alert = AlertFired(project="payments")
        await events.emit(alert, event_id="p1")
        alert.project = "search"
        to_payments = await poll_events_mcp(
            c, "alert.fired", {"project": "payments"}, cursor=payments.cursor
        )
        to_search = await poll_events_mcp(
            c, "alert.fired", {"project": "search"}, cursor=search.cursor
        )
    assert [e.event_id for e in to_payments.events] == ["p1"]
    assert to_payments.events[0].data["project"] == "payments"
    assert to_search.events == []


async def test_each_subscription_gets_its_own_copy():
    server, events = make_server()
    async with client(server) as c:
        full = await poll_events_mcp(c, "alert.fired")
        quiet = await poll_events_mcp(c, "alert.fired", {"include_message": False})
        await events.emit(AlertFired(project="api", message="disk full"), event_id="a1")
        to_quiet = await poll_events_mcp(
            c, "alert.fired", {"include_message": False}, cursor=quiet.cursor
        )
        to_full = await poll_events_mcp(c, "alert.fired", cursor=full.cursor)
        replay = await poll_events_mcp(
            c, "alert.fired", {"include_message": False}, cursor=quiet.cursor
        )
    assert to_quiet.events[0].data["message"] is None
    assert to_full.events[0].data["message"] == "disk full"
    assert replay.events[0].data["message"] is None
    assert to_quiet.events[0].event_id == to_full.events[0].event_id == "a1"


def test_for_subscription_must_return_the_model_or_none():
    class Bad(BaseModel):
        x: int

        def for_subscription(self) -> Bad | None:
            return True  # ty: ignore[invalid-explicit-return]

    server = FastMCP("bad")
    events = EventsExtension()
    server.add_extension(events)
    events.event("bad.event")(Bad)

    async def run():
        async with client(server) as c:
            start = await poll_events_mcp(c, "bad.event")
            await events.emit(Bad(x=1))
            await poll_events_mcp(c, "bad.event", cursor=start.cursor)

    # A bug in the server's hook, so the client sees only an internal error.
    with pytest.raises(MCPError, match="Internal"):
        anyio.run(run)


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


async def test_arguments_that_do_not_fit_for_subscription_are_rejected():
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


async def test_a_model_without_for_subscription_takes_no_arguments():
    server = FastMCP("deploys")
    events = EventsExtension()
    server.add_extension(events)

    @events.event("deploy.finished", description="A deploy finished")
    class DeployFinished(BaseModel):
        service: str

    async with client(server) as c:
        [event_type] = await list_events(c)
        start = await poll_events_mcp(c, "deploy.finished")
        await events.emit(DeployFinished(service="api"), event_id="d1")
        result = await poll_events_mcp(c, "deploy.finished", cursor=start.cursor)
    assert event_type.input_schema is None
    assert event_type.description == "A deploy finished"
    assert [e.event_id for e in result.events] == ["d1"]


def test_emitting_an_unregistered_model_is_an_error():
    _, events = make_server()

    class Other(BaseModel):
        x: int

    with pytest.raises(ValueError, match="not a registered event model"):
        anyio.run(lambda: events.emit(Other(x=1)))


def test_a_model_registers_as_one_event_type_only():
    _, events = make_server()
    with pytest.raises(ValueError, match="already the event"):
        events.event("alert.again")(AlertFired)


def test_for_subscription_must_be_a_sync_instance_method():
    events = EventsExtension()

    class Async(BaseModel):
        x: int

        async def for_subscription(self, x: int | None = None) -> Async:
            return self

    with pytest.raises(TypeError, match="must not be async"):
        events.event("async.event")(Async)


class Deploy(BaseModel):
    """A deploy finished."""

    project_id: str = Field(alias="projectId")
    details: dict[str, Any]

    def for_subscription(self, redact: bool = False) -> Deploy:
        if redact:
            self.details["nested"].pop("secret", None)
        return self


class ApiDeploy(Deploy):
    """A deploy of the API finished."""


async def test_aliases_and_nested_values_survive_delivery():
    server = FastMCP("deploys")
    events = EventsExtension()
    server.add_extension(events)
    events.event("deploy.finished")(Deploy)
    payload = Deploy(projectId="p1", details={"nested": {"secret": "s"}})
    async with client(server) as c:
        (listed,) = await list_events(c)
        start = await poll_events_mcp(c, "deploy.finished")
        await events.emit(payload, event_id="d1")
        redacted = await poll_events_mcp(
            c, "deploy.finished", {"redact": True}, cursor=start.cursor
        )
        full = await poll_events_mcp(c, "deploy.finished", cursor=start.cursor)
    assert listed.payload_schema is not None
    assert "projectId" in listed.payload_schema["properties"]
    assert redacted.events[0].data == {"projectId": "p1", "details": {"nested": {}}}
    # The redacting hook changed a nested value of its own copy only.
    assert full.events[0].data["details"] == {"nested": {"secret": "s"}}


async def test_an_inherited_for_subscription_is_the_hook():
    server = FastMCP("deploys")
    events = EventsExtension()
    server.add_extension(events)
    events.event("api.deploy.finished")(ApiDeploy)
    async with client(server) as c:
        (listed,) = await list_events(c)
    assert listed.input_schema is not None
    assert set(listed.input_schema["properties"]) == {"redact"}
