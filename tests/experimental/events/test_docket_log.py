"""Emitted events kept in Docket's Redis, shared by every server on the same log."""

from __future__ import annotations

import uuid

import anyio
import pytest
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.experimental.events import (
    EventsClientExtension,
    EventsExtension,
    poll_events_mcp,
    subscribe_events,
)

pytest.importorskip("docket")

from fastmcp.experimental.events.docket_log import DocketEventLog  # noqa: E402


class AlertFired(BaseModel):
    """An alert fired."""

    project: str

    def matches(self, project: str | None = None) -> bool:
        return project is None or self.project == project


@pytest.fixture
def url() -> str:
    # Each test gets its own in-memory Redis, shared by the servers it builds.
    return f"memory://{uuid.uuid4().hex}"


def make_server(url: str, **extension_kwargs) -> tuple[FastMCP, EventsExtension]:
    server = FastMCP("alerts")
    events = EventsExtension(log=DocketEventLog(url), **extension_kwargs)
    server.add_extension(events)
    events.event("alert.fired")(AlertFired)
    return server, events


def client(server: FastMCP) -> Client:
    return Client(server, extensions=[EventsClientExtension()])


async def test_poll_reads_what_was_emitted(url: str):
    server, events = make_server(url)
    async with client(server) as c:
        start = await poll_events_mcp(c, "alert.fired", {"project": "api"})
        await events.emit(AlertFired(project="api"), event_id="a1")
        await events.emit(AlertFired(project="web"), event_id="w1")
        await events.emit(AlertFired(project="api"), event_id="a2")
        api = await poll_events_mcp(
            c, "alert.fired", {"project": "api"}, cursor=start.cursor
        )
        again = await poll_events_mcp(c, "alert.fired", cursor=api.cursor)
    assert [e.event_id for e in api.events] == ["a1", "a2"]
    assert api.events[0].data == {"project": "api"}
    assert not api.truncated
    assert again.events == []


async def test_a_cursor_from_one_server_works_on_another(url: str):
    first, first_events = make_server(url)
    second, _ = make_server(url)
    async with client(first) as c:
        start = await poll_events_mcp(c, "alert.fired")
        await first_events.emit(AlertFired(project="api"), event_id="a1")
    async with client(second) as c:
        result = await poll_events_mcp(c, "alert.fired", cursor=start.cursor)
    assert [e.event_id for e in result.events] == ["a1"]
    assert not result.truncated


async def test_max_events_pages_through_the_log(url: str):
    server, events = make_server(url)
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


async def test_a_cursor_from_another_log_replays_it_as_a_gap(url: str):
    server, events = make_server(url)
    async with client(server) as c:
        await events.emit(AlertFired(project="api"), event_id="a1")
        result = await poll_events_mcp(c, "alert.fired", cursor="0123456789ab-41")
    assert result.truncated
    assert [e.event_id for e in result.events] == ["a1"]


async def test_push_wakes_on_an_emit_from_another_server(url: str):
    server, _ = make_server(url, poll_interval_ms=60_000)
    _, other_events = make_server(url)
    received = []
    async with client(server) as c:
        subscription = subscribe_events(
            c, "alert.fired", {"project": "api"}, delivery="push"
        )

        async def consume():
            async for event in subscription:
                received.append(event.event_id)
                return

        with anyio.fail_after(3):
            async with anyio.create_task_group() as tg:
                tg.start_soon(consume)
                while subscription.cursor is None:
                    await anyio.sleep(0.01)
                # The other server never started, so open its log by hand.
                async with other_events._log.lifespan():
                    await other_events.emit(AlertFired(project="api"), event_id="a1")
    assert received == ["a1"]


def test_a_log_needs_a_url_or_a_docket():
    with pytest.raises(ValueError, match="exactly one"):
        DocketEventLog()
