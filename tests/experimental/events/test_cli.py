import json
from typing import Any, Literal
from unittest.mock import patch

import anyio
import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli import client as client_module
from fastmcp.cli.events import list_events_command, watch_command
from fastmcp.experimental.events import (
    Event,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
)


def make_server(
    log: list[dict[str, Any]], subscribed: anyio.Event | None = None
) -> FastMCP:
    server = FastMCP("alerts")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)

    @events.event(
        name="alert.fired",
        description="An alert fired",
        input_schema={
            "type": "object",
            "properties": {"min_level": {"type": "integer"}},
        },
    )
    def alert_fired(poll: EventPoll) -> EventBatch:
        if poll.cursor is None:
            if subscribed is not None:
                subscribed.set()
            return EventBatch(cursor=str(len(log)))
        floor = poll.arguments.get("min_level", 0)
        fired = [
            Event(data=entry, event_id=entry["key"])
            for entry in log[int(poll.cursor) :]
            if entry["level"] >= floor
        ]
        return EventBatch(events=fired, cursor=str(len(log)))

    return server


def patch_client(server: FastMCP):
    def fake_build_client(resolved: Any, *, events: bool = False, **_: Any) -> Client:
        return Client(server, extensions=[EventsClientExtension()] if events else None)

    return (
        patch.object(client_module, "resolve_server_spec", return_value="fake"),
        patch.object(client_module, "_build_client", side_effect=fake_build_client),
    )


async def test_list_shows_event_types(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(make_server([]))
    with resolve, build:
        await list_events_command("fake")
    out = capsys.readouterr().out
    assert "Events (1)" in out
    assert "alert.fired" in out
    assert "poll, push" in out
    assert "min_level: int" in out


async def test_list_json(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(make_server([]))
    with resolve, build:
        await list_events_command("fake", json_output=True)
    (entry,) = json.loads(capsys.readouterr().out)["events"]
    assert entry["name"] == "alert.fired"
    assert entry["delivery"] == ["poll", "push"]


@pytest.mark.parametrize("delivery", ["poll", "push"])
async def test_watch_prints_events_and_the_cursor_to_resume_from(
    delivery: Literal["poll", "push"], capsys: pytest.CaptureFixture[str]
):
    log = [{"key": "old", "level": 9}]
    subscribed = anyio.Event()
    resolve, build = patch_client(make_server(log, subscribed))

    async def fire() -> None:
        # Only after the watch has taken its starting cursor, or these would
        # count as history and never be delivered.
        await subscribed.wait()
        log.append({"key": "quiet", "level": 1})
        log.append({"key": "loud", "level": 5})

    with resolve, build, anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(fire)
            await watch_command(
                "fake",
                "alert.fired",
                "min_level=3",
                count=1,
                delivery=delivery,
                json_output=True,
                min_poll_interval=0.01,
            )

    captured = capsys.readouterr()
    (line,) = captured.out.splitlines()
    record = json.loads(line)
    # Subscribing starts from now, so "old" is not replayed; "quiet" is
    # filtered out by the coerced integer argument.
    assert record["eventId"] == "loud"
    assert record["data"] == {"key": "loud", "level": 5}
    assert record["cursor"] == "3"
    assert "resume with --cursor 3" in captured.err


async def test_watch_resumes_from_a_cursor(capsys: pytest.CaptureFixture[str]):
    log = [{"key": "a", "level": 1}, {"key": "b", "level": 1}]
    resolve, build = patch_client(make_server(log))
    with resolve, build, anyio.fail_after(10):
        await watch_command(
            "fake",
            "alert.fired",
            cursor="1",
            count=1,
            delivery="poll",
            json_output=True,
        )
    assert json.loads(capsys.readouterr().out)["eventId"] == "b"


async def test_watch_unknown_event_exits(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(make_server([]))
    with resolve, build, pytest.raises(SystemExit):
        await watch_command("fake", "nope")
    assert "Unknown event" in capsys.readouterr().out


async def test_watch_without_server_support_exits(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(FastMCP("plain"))
    with resolve, build, pytest.raises(SystemExit):
        await watch_command("fake", "alert.fired")
    assert "does not advertise the Events extension" in capsys.readouterr().out


async def test_watch_escapes_markup_in_what_the_server_sends(
    capsys: pytest.CaptureFixture[str],
):
    server = FastMCP("hostile")
    events = EventsExtension(poll_interval_ms=10)
    server.add_extension(events)

    @events.event(name="tick")
    def tick(poll: EventPoll) -> EventBatch:
        return EventBatch(
            events=[Event(data={}, event_id="e1")], cursor="[link=https://x.test]c"
        )

    resolve, build = patch_client(server)
    with resolve, build, anyio.fail_after(10):
        await watch_command("fake", "tick", cursor="0", count=1, delivery="poll")

    captured = capsys.readouterr()
    assert "cursor [link=https://x.test]c" in captured.out
    assert "--cursor [link=https://x.test]c" in captured.err
