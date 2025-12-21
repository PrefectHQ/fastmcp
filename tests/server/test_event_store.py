"""Tests for the EventStore implementation."""

import pytest
from mcp.server.streamable_http import EventMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest

from fastmcp.server.event_store import EventEntry, EventStore, StreamEventList


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
    @pytest.fixture
    def event_store(self):
        return EventStore(max_events_per_stream=5, ttl=3600)

    @pytest.fixture
    def sample_message(self):
        return JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", method="test", id=1))

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
        await event_store.store_event("stream-1", sample_message)

        # Replay events after the first one
        replayed_events: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed_events.append(event)

        stream_id = await event_store.replay_events_after(first_event_id, callback)
        assert stream_id == "stream-1"
        assert len(replayed_events) == 1

    async def test_replay_events_after_skips_priming_events(self, event_store):
        """Priming events (message=None) should not be replayed."""
        # Store a priming event
        priming_id = await event_store.store_event("stream-1", None)

        # Store a real event
        real_message = JSONRPCMessage(
            root=JSONRPCRequest(jsonrpc="2.0", method="test", id=1)
        )
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
            msg = JSONRPCMessage(
                root=JSONRPCRequest(jsonrpc="2.0", method=f"test-{i}", id=i)
            )
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
        msg1 = JSONRPCMessage(
            root=JSONRPCRequest(jsonrpc="2.0", method="stream1-test", id=1)
        )
        msg2 = JSONRPCMessage(
            root=JSONRPCRequest(jsonrpc="2.0", method="stream2-test", id=2)
        )

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

    async def test_default_storage_is_memory(self):
        """Test that EventStore defaults to in-memory storage."""
        event_store = EventStore()
        msg = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", method="test", id=1))

        event_id = await event_store.store_event("stream-1", msg)
        assert event_id is not None

        replayed: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed.append(event)

        # Store another event and replay
        await event_store.store_event("stream-1", msg)
        await event_store.replay_events_after(event_id, callback)
        assert len(replayed) == 1


class TestEventStoreIntegration:
    """Integration tests for EventStore with actual message types."""

    async def test_roundtrip_jsonrpc_message(self):
        event_store = EventStore()

        # Create a realistic JSON-RPC request wrapped in JSONRPCMessage
        original_msg = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                method="tools/call",
                id="request-123",
                params={"name": "my_tool", "arguments": {"x": 1, "y": 2}},
            )
        )

        # Store it
        event_id = await event_store.store_event("stream-1", original_msg)

        # Store another event so we have something to replay
        second_msg = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                method="tools/call",
                id="request-456",
                params={"name": "my_tool", "arguments": {"x": 3, "y": 4}},
            )
        )
        await event_store.store_event("stream-1", second_msg)

        # Replay and verify the message content
        replayed: list[EventMessage] = []

        async def callback(event: EventMessage):
            replayed.append(event)

        await event_store.replay_events_after(event_id, callback)

        assert len(replayed) == 1
        assert isinstance(replayed[0].message.root, JSONRPCRequest)
        assert replayed[0].message.root.method == "tools/call"
        assert replayed[0].message.root.id == "request-456"
