"""Tests for the EventStore implementation."""

import asyncio

import pytest
from mcp.server.streamable_http import EventMessage
from mcp_types import JSONRPCRequest

from fastmcp.server.event_store import (
    _LOCK_STRIPES,
    EventEntry,
    EventStore,
    SessionScopedEventStore,
    StreamEventList,
)
from fastmcp.server.http import FastMCPStreamableHTTPSessionManager


class TestEventEntry:
    def test_event_entry_with_message(self):
        entry = EventEntry(
            event_id="event-1",
            stream_id="stream-1",
            message={"jsonrpc": "2.0", "method": "test", "id": 1},
        )
        assert entry.event_id == "event-1"
        assert entry.stream_id == "stream-1"
        assert entry.message == {"jsonrpc": "2.0", "method": "test", "id": 1}

    def test_event_entry_without_message(self):
        entry = EventEntry(
            event_id="event-1",
            stream_id="stream-1",
            message=None,
        )
        assert entry.message is None


class TestStreamEventList:
    def test_stream_event_list(self):
        stream_list = StreamEventList(event_ids=["event-1", "event-2", "event-3"])
        assert stream_list.event_ids == ["event-1", "event-2", "event-3"]

    def test_stream_event_list_empty(self):
        stream_list = StreamEventList(event_ids=[])
        assert stream_list.event_ids == []


class TestEventStore:
    @pytest.mark.parametrize("max_events_per_stream", [0, -1])
    def test_max_events_per_stream_must_be_positive(self, max_events_per_stream):
        with pytest.raises(
            ValueError, match="max_events_per_stream must be at least 1"
        ):
            EventStore(max_events_per_stream=max_events_per_stream)

    @pytest.fixture
    def event_store(self):
        return EventStore(max_events_per_stream=5, ttl=3600)

    @pytest.fixture
    def sample_message(self):
        return JSONRPCRequest(jsonrpc="2.0", method="test", id=1)

    async def test_store_event_returns_event_id(self, event_store, sample_message):
        event_id = await event_store.store_event("stream-1", sample_message)
        assert event_id is not None
        assert isinstance(event_id, str)
        assert len(event_id) > 0

    async def test_store_event_priming_event(self, event_store):
        """Test storing a priming event (message=None)."""
        event_id = await event_store.store_event("stream-1", None)
        assert event_id is not None

    async def test_store_multiple_events(self, event_store, sample_message):
        event_ids = []
        for _ in range(3):
            event_id = await event_store.store_event("stream-1", sample_message)
            event_ids.append(event_id)

        # All event IDs should be unique
        assert len(set(event_ids)) == 3

    async def test_replay_events_after_returns_stream_id(
        self, event_store, sample_message
    ):
        # Store some events
        first_event_id = await event_store.store_event("stream-1", sample_message)
        second_event_id = await event_store.store_event("stream-1", sample_message)

        # Replay events after the first one
        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        stream_id = await event_store.replay_events_after(first_event_id, callback)
        assert stream_id == "stream-1"
        assert len(replayed_events) == 1
        assert replayed_events[0].event_id == second_event_id
        replayed_message = replayed_events[0].message
        assert isinstance(replayed_message, JSONRPCRequest)
        assert replayed_message.method == "test"

    async def test_replay_events_after_skips_priming_events(self, event_store):
        """Priming events (message=None) should not be replayed."""
        # Store a priming event
        priming_id = await event_store.store_event("stream-1", None)

        # Store a real event
        real_message = JSONRPCRequest(jsonrpc="2.0", method="test", id=1)
        await event_store.store_event("stream-1", real_message)

        # Replay after priming event
        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        await event_store.replay_events_after(priming_id, callback)

        # Only the real event should be replayed
        assert len(replayed_events) == 1

    async def test_replay_events_after_unknown_event_id(self, event_store):
        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        result = await event_store.replay_events_after("unknown-event-id", callback)
        assert result is None
        assert len(replayed_events) == 0

    async def test_max_events_per_stream_trims_old_events(self, event_store):
        """Test that old events are trimmed when max_events_per_stream is exceeded."""
        # Store more events than the limit
        event_ids = []
        for i in range(7):
            msg = JSONRPCRequest(jsonrpc="2.0", method=f"test-{i}", id=i)
            event_id = await event_store.store_event("stream-1", msg)
            event_ids.append(event_id)

        # The first 2 events should have been trimmed (7 - 5 = 2)
        # Trying to replay from the first event should fail
        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        result = await event_store.replay_events_after(event_ids[0], callback)
        assert result is None  # First event was trimmed

        # But replaying from a more recent event should work
        result = await event_store.replay_events_after(event_ids[3], callback)
        assert result == "stream-1"

    async def test_multiple_streams_are_isolated(self, event_store):
        """Events from different streams should not interfere with each other."""
        msg1 = JSONRPCRequest(jsonrpc="2.0", method="stream1-test", id=1)
        msg2 = JSONRPCRequest(jsonrpc="2.0", method="stream2-test", id=2)

        stream1_event = await event_store.store_event("stream-1", msg1)
        await event_store.store_event("stream-1", msg1)

        stream2_event = await event_store.store_event("stream-2", msg2)
        await event_store.store_event("stream-2", msg2)

        # Replay stream 1
        stream1_replayed: list[EventMessage] = []

        async def callback1(event: EventMessage):
            stream1_replayed.append(event)

        stream_id = await event_store.replay_events_after(stream1_event, callback1)
        assert stream_id == "stream-1"
        assert len(stream1_replayed) == 1

        # Replay stream 2
        stream2_replayed: list[EventMessage] = []

        async def callback2(event: EventMessage):
            stream2_replayed.append(event)

        stream_id = await event_store.replay_events_after(stream2_event, callback2)
        assert stream_id == "stream-2"
        assert len(stream2_replayed) == 1

    @pytest.mark.parametrize("stream_id", ["_GET_stream", "1"])
    async def test_session_scoped_stores_isolate_overlapping_stream_ids(
        self, event_store, stream_id
    ):
        session_a_store = SessionScopedEventStore(event_store, "session-a")
        session_b_store = SessionScopedEventStore(event_store, "session-b")
        msg_a1 = JSONRPCRequest(jsonrpc="2.0", method="session-a-1", id=1)
        msg_a2 = JSONRPCRequest(jsonrpc="2.0", method="session-a-2", id=2)
        msg_b = JSONRPCRequest(jsonrpc="2.0", method="session-b", id=3)

        session_a_event = await session_a_store.store_event(stream_id, msg_a1)
        await session_b_store.store_event(stream_id, msg_b)
        session_a_second_event = await session_a_store.store_event(stream_id, msg_a2)

        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        replayed_stream_id = await session_a_store.replay_events_after(
            session_a_event, callback
        )

        assert replayed_stream_id == stream_id
        assert [event.event_id for event in replayed_events] == [session_a_second_event]
        replayed_message = replayed_events[0].message
        assert isinstance(replayed_message, JSONRPCRequest)
        assert replayed_message.method == "session-a-2"

    async def test_session_scoped_replay_rejects_foreign_last_event_id(
        self, event_store
    ):
        session_a_store = SessionScopedEventStore(event_store, "session-a")
        session_b_store = SessionScopedEventStore(event_store, "session-b")
        msg_b1 = JSONRPCRequest(jsonrpc="2.0", method="session-b-1", id=1)
        msg_b2 = JSONRPCRequest(jsonrpc="2.0", method="session-b-2", id=2)

        foreign_event_id = await session_b_store.store_event("_GET_stream", msg_b1)
        await session_b_store.store_event("_GET_stream", msg_b2)

        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        replayed_stream_id = await session_a_store.replay_events_after(
            foreign_event_id, callback
        )

        assert replayed_stream_id is None
        assert replayed_events == []

    def test_session_manager_returns_scoped_event_stores(self, event_store):
        session_manager = FastMCPStreamableHTTPSessionManager(
            app=object(), event_store=event_store
        )

        first_transport_store = session_manager.event_store
        second_transport_store = session_manager.event_store

        assert isinstance(first_transport_store, SessionScopedEventStore)
        assert isinstance(second_transport_store, SessionScopedEventStore)
        assert first_transport_store is not second_transport_store

    async def test_default_storage_is_memory(self):
        """Test that EventStore defaults to in-memory storage."""
        event_store = EventStore()
        msg = JSONRPCRequest(jsonrpc="2.0", method="test", id=1)

        event_id = await event_store.store_event("stream-1", msg)
        assert event_id is not None

        replayed: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed.append(event)

        # Store another event and replay
        await event_store.store_event("stream-1", msg)
        await event_store.replay_events_after(event_id, callback)
        assert len(replayed) == 1


class TestConcurrentStoreEvent:
    async def test_concurrent_stores_on_one_stream(self, monkeypatch):
        """Concurrent stores must not lose events or evict the same ID twice.

        A live session stores events from more than one task (the SSE writer and
        the message router), so the stream's event list is read and written
        concurrently. Interleaved, each task appends only its own ID to the list
        it read, and both evict the same expired IDs -- the second delete is the
        one that raised `FileNotFoundError` on a file-backed store.
        """
        event_store = EventStore(max_events_per_stream=2)

        stream_get = event_store._stream_store.get
        event_delete = event_store._event_store.delete
        deleted: list[str] = []

        async def yielding_get(**kwargs):
            # Suspend between the read and the write so the tasks interleave.
            stream_data = await stream_get(**kwargs)
            await asyncio.sleep(0)
            return stream_data

        async def recording_delete(**kwargs):
            deleted.append(kwargs["key"])
            return await event_delete(**kwargs)

        monkeypatch.setattr(event_store._stream_store, "get", yielding_get)
        monkeypatch.setattr(event_store._event_store, "delete", recording_delete)

        message = JSONRPCRequest(jsonrpc="2.0", method="test", id=1)
        event_ids = await asyncio.gather(
            *(event_store.store_event("stream-1", message) for _ in range(5))
        )

        stream_data = await stream_get(key="stream-1")
        assert stream_data is not None
        # The two most recent events are retained; every other ID was evicted
        # exactly once, and no ID vanished without being evicted.
        assert len(stream_data.event_ids) == 2
        assert sorted(stream_data.event_ids + deleted) == sorted(event_ids)
        assert len(deleted) == len(set(deleted))

    async def test_distinct_streams_are_not_serialized(self, monkeypatch):
        """Unrelated streams must not wait on each other's backend calls.

        One EventStore is shared by every session, so a store-wide lock would
        put a Redis round-trip for one session in front of every other one.
        """
        event_store = EventStore()

        # hash() is salted per process, so pick the second stream at runtime.
        first = "stream-a"
        second = next(
            candidate
            for candidate in (f"stream-{i}" for i in range(1000))
            if hash(candidate) % _LOCK_STRIPES != hash(first) % _LOCK_STRIPES
        )

        stream_get = event_store._stream_store.get
        both_inside = asyncio.Event()
        inside = 0

        async def gate(**kwargs):
            nonlocal inside
            inside += 1
            if inside == 2:
                both_inside.set()
            # Both critical sections have to be open at once; a store-wide lock
            # would keep the second task out until the first finished.
            await asyncio.wait_for(both_inside.wait(), timeout=2)
            return await stream_get(**kwargs)

        monkeypatch.setattr(event_store._stream_store, "get", gate)

        message = JSONRPCRequest(jsonrpc="2.0", method="test", id=1)
        await asyncio.gather(
            event_store.store_event(first, message),
            event_store.store_event(second, message),
        )


class TestEventStoreIntegration:
    """Integration tests for EventStore with actual message types."""

    async def test_roundtrip_jsonrpc_message(self):
        event_store = EventStore()

        # Create a realistic JSON-RPC request wrapped in JSONRPCMessage
        original_msg = JSONRPCRequest(
            jsonrpc="2.0",
            method="tools/call",
            id="request-123",
            params={"name": "my_tool", "arguments": {"x": 1, "y": 2}},
        )

        # Store it
        event_id = await event_store.store_event("stream-1", original_msg)

        # Store another event so we have something to replay
        second_msg = JSONRPCRequest(
            jsonrpc="2.0",
            method="tools/call",
            id="request-456",
            params={"name": "my_tool", "arguments": {"x": 3, "y": 4}},
        )
        await event_store.store_event("stream-1", second_msg)

        # Replay and verify the message content
        replayed: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed.append(event)

        await event_store.replay_events_after(event_id, callback)

        assert len(replayed) == 1
        assert replayed[0].event_id is not None
        assert isinstance(replayed[0].message, JSONRPCRequest)
        assert replayed[0].message.method == "tools/call"
        assert replayed[0].message.id == "request-456"
