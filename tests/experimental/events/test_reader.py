"""Event types declared as a model and read from an upstream with `events.reader`."""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.experimental.events import (
    Event,
    EventBatch,
    EventPoll,
    EventsClientExtension,
    EventsExtension,
    list_events,
    poll_events_mcp,
    subscribe_events,
)


class EmailReceived(BaseModel):
    """A message arrived in the mailbox."""

    message_id: str
    sender: str | None

    def for_subscription(
        self, sender: str | None = None, redact: bool = False
    ) -> EmailReceived | None:
        if sender is not None and self.sender != sender:
            return None
        if redact:
            return self.model_copy(update={"sender": None})
        return self


def make_server(
    mailbox: list[EmailReceived], **extension_kwargs
) -> tuple[FastMCP, EventsExtension, list[EventPoll]]:
    server = FastMCP("mail")
    events = EventsExtension(**extension_kwargs)
    server.add_extension(events)
    events.event("email.received")(EmailReceived)
    polls: list[EventPoll] = []

    @events.reader(EmailReceived)
    async def read_mailbox(poll: EventPoll) -> EventBatch:
        polls.append(poll)
        if poll.cursor is None:
            return EventBatch(cursor=str(len(mailbox)))
        new = mailbox[int(poll.cursor) :]
        return EventBatch(
            events=[Event(data=m.model_dump(), event_id=m.message_id) for m in new],
            cursor=str(len(mailbox)),
        )

    return server, events, polls


def client(server: FastMCP) -> Client:
    return Client(server, extensions=[EventsClientExtension()])


async def test_the_model_declares_a_read_event_type():
    server, _, _ = make_server([])
    async with client(server) as c:
        [event_type] = await list_events(c)
    assert event_type.name == "email.received"
    assert event_type.input_schema is not None
    assert set(event_type.input_schema["properties"]) == {"sender", "redact"}
    assert event_type.payload_schema is not None


async def test_the_reader_reads_and_for_subscription_shapes():
    mailbox: list[EmailReceived] = []
    server, _, polls = make_server(mailbox)
    async with client(server) as c:
        start = await poll_events_mcp(c, "email.received", {"sender": "a@x.io"})
        mailbox.append(EmailReceived(message_id="m1", sender="a@x.io"))
        mailbox.append(EmailReceived(message_id="m2", sender="b@x.io"))
        from_a = await poll_events_mcp(
            c, "email.received", {"sender": "a@x.io"}, cursor=start.cursor
        )
        redacted = await poll_events_mcp(
            c, "email.received", {"redact": True}, cursor=start.cursor
        )
    assert [e.event_id for e in from_a.events] == ["m1"]
    assert from_a.cursor == "2"
    assert [e.data["sender"] for e in redacted.events] == [None, None]
    # The reader sees the validated arguments, to narrow its query.
    assert polls[1].arguments == {"sender": "a@x.io", "redact": False}


async def test_a_read_event_that_does_not_fit_the_model_fails_the_poll():
    mailbox: list[EmailReceived] = []
    server, events, _ = make_server(mailbox)
    async with client(server) as c:
        start = await poll_events_mcp(c, "email.received")
        mailbox.append(EmailReceived.model_construct(message_id="m1"))
        with pytest.raises(MCPError):
            await poll_events_mcp(c, "email.received", cursor=start.cursor)


async def test_push_reads_on_the_interval():
    mailbox: list[EmailReceived] = []
    server, _, _ = make_server(mailbox, poll_interval_ms=50)
    received = []
    async with client(server) as c:
        subscription = subscribe_events(c, "email.received", delivery="push")

        async def consume():
            async for event in subscription:
                received.append(event.event_id)
                return

        with anyio.fail_after(3):
            async with anyio.create_task_group() as tg:
                tg.start_soon(consume)
                while subscription.cursor is None:
                    await anyio.sleep(0.01)
                mailbox.append(EmailReceived(message_id="m1", sender="a@x.io"))
    assert received == ["m1"]


def test_a_read_type_is_not_emitted_into():
    _, events, _ = make_server([])
    with pytest.raises(ValueError, match="read from an upstream"):
        anyio.run(lambda: events.emit(EmailReceived(message_id="m1", sender=None)))


def test_a_reader_needs_a_registered_model():
    events = EventsExtension()
    with pytest.raises(ValueError, match="not a registered event model"):
        events.reader(EmailReceived)


def test_a_type_has_one_reader():
    _, events, _ = make_server([])
    with pytest.raises(ValueError, match="already has a reader"):

        @events.reader(EmailReceived)
        def again(poll: EventPoll) -> EventBatch:
            return EventBatch()
