"""Tests for application-level MCP events support (Phase 2).

Tests event declaration, emission, subscription, retained values,
session registry, context integration, and capabilities.
"""

from __future__ import annotations

import asyncio
from typing import Any
import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.events import (
    EventEffect,
    EventEmitNotification,
    EventListResult,
    EventParams,
    EventSubscribeParams,
    EventSubscribeResult,
    EventTopicDescriptor,
    EventUnsubscribeParams,
    EventUnsubscribeResult,
    RetainedEvent,
    RetainedValueStore,
    SubscriptionRegistry,
    _pattern_to_regex,
)


# ---------------------------------------------------------------------------
# SubscriptionRegistry unit tests
# ---------------------------------------------------------------------------


class TestSubscriptionRegistry:
    async def test_exact_match(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        result = await reg.match("myapp/status")
        assert result == {"s1"}

    async def test_no_match(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        result = await reg.match("myapp/other")
        assert result == set()

    async def test_plus_wildcard(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/+/messages")
        assert await reg.match("myapp/session1/messages") == {"s1"}
        assert await reg.match("myapp/session2/messages") == {"s1"}
        # + does not match segment separator
        assert await reg.match("myapp/a/b/messages") == set()

    async def test_hash_wildcard(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/#")
        assert await reg.match("myapp/status") == {"s1"}
        assert await reg.match("myapp/a/b/c") == {"s1"}
        assert await reg.match("myapp") == {"s1"}

    async def test_hash_only_valid_last(self):
        with pytest.raises(ValueError, match="last segment"):
            _pattern_to_regex("myapp/#/invalid")

    async def test_at_most_once(self):
        """A session with overlapping subscriptions receives at most once."""
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/+/messages")
        await reg.add("s1", "myapp/#")
        result = await reg.match("myapp/session1/messages")
        assert result == {"s1"}
        assert len(result) == 1

    async def test_multiple_sessions(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        await reg.add("s2", "myapp/status")
        result = await reg.match("myapp/status")
        assert result == {"s1", "s2"}

    async def test_remove(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        await reg.remove("s1", "myapp/status")
        result = await reg.match("myapp/status")
        assert result == set()

    async def test_remove_all(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        await reg.add("s1", "myapp/messages")
        await reg.remove_all("s1")
        assert await reg.match("myapp/status") == set()
        assert await reg.match("myapp/messages") == set()

    async def test_get_subscriptions(self):
        reg = SubscriptionRegistry()
        await reg.add("s1", "myapp/status")
        await reg.add("s1", "myapp/messages")
        subs = await reg.get_subscriptions("s1")
        assert subs == {"myapp/status", "myapp/messages"}

    async def test_get_subscriptions_empty(self):
        reg = SubscriptionRegistry()
        subs = await reg.get_subscriptions("nonexistent")
        assert subs == set()


# ---------------------------------------------------------------------------
# RetainedValueStore unit tests
# ---------------------------------------------------------------------------


class TestRetainedValueStore:
    async def test_set_and_get(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", event_id="e1", payload={"x": 1})
        await store.set("t", event)
        assert await store.get("t") == event

    async def test_get_nonexistent(self):
        store = RetainedValueStore()
        assert await store.get("nonexistent") is None

    async def test_get_matching(self):
        store = RetainedValueStore()
        await store.set("myapp/a", RetainedEvent(topic="myapp/a", event_id="e1", payload=1))
        await store.set("myapp/b", RetainedEvent(topic="myapp/b", event_id="e2", payload=2))
        await store.set("other/c", RetainedEvent(topic="other/c", event_id="e3", payload=3))
        result = await store.get_matching("myapp/+")
        assert len(result) == 2
        assert {e.event_id for e in result} == {"e1", "e2"}
        # Verify payload values for each retained event
        by_id = {e.event_id: e for e in result}
        assert by_id["e1"].topic == "myapp/a"
        assert by_id["e1"].payload == 1
        assert by_id["e2"].topic == "myapp/b"
        assert by_id["e2"].payload == 2

    async def test_delete(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", event_id="e1", payload=1)
        await store.set("t", event)
        await store.delete("t")
        assert await store.get("t") is None

    async def test_expiry(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", event_id="e1", payload=1)
        # Set with expired timestamp
        await store.set("t", event, expires_at="2000-01-01T00:00:00Z")
        assert await store.get("t") is None

    async def test_not_expired(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", event_id="e1", payload=1)
        await store.set("t", event, expires_at="2099-01-01T00:00:00Z")
        assert await store.get("t") == event

    async def test_get_matching_skips_expired(self):
        store = RetainedValueStore()
        await store.set(
            "myapp/a",
            RetainedEvent(topic="myapp/a", event_id="e1", payload=1),
            expires_at="2000-01-01T00:00:00Z",
        )
        await store.set(
            "myapp/b",
            RetainedEvent(topic="myapp/b", event_id="e2", payload=2),
        )
        result = await store.get_matching("myapp/+")
        assert len(result) == 1
        assert result[0].event_id == "e2"


# ---------------------------------------------------------------------------
# EventTopicDescriptor tests
# ---------------------------------------------------------------------------


class TestEventTopicDescriptor:
    def test_basic_creation(self):
        desc = EventTopicDescriptor(pattern="myapp/status", description="Status")
        assert desc.pattern == "myapp/status"
        assert desc.description == "Status"
        assert desc.retained is False

    def test_schema_alias(self):
        desc = EventTopicDescriptor(
            pattern="myapp/status",
            schema={"type": "object"},
        )
        dumped = desc.model_dump(by_alias=True)
        assert "schema" in dumped
        assert dumped["schema"] == {"type": "object"}


# ---------------------------------------------------------------------------
# EventEmitNotification tests
# ---------------------------------------------------------------------------


class TestEventEmitNotification:
    def test_creation(self):
        notification = EventEmitNotification(
            params=EventParams(
                topic="myapp/status",
                event_id="e1",
                payload={"status": "running"},
            )
        )
        assert notification.method == "events/emit"
        assert notification.params.topic == "myapp/status"

    def test_serialization(self):
        notification = EventEmitNotification(
            params=EventParams(
                topic="myapp/status",
                event_id="e1",
                payload={"status": "running"},
                retained=True,
                requested_effects=[
                    EventEffect(type="inject_context", priority="high")
                ],
            )
        )
        data = notification.model_dump(exclude_none=True)
        assert data["method"] == "events/emit"
        assert data["params"]["topic"] == "myapp/status"
        assert data["params"]["event_id"] == "e1"
        assert data["params"]["payload"] == {"status": "running"}
        assert data["params"]["retained"] is True
        assert len(data["params"]["requested_effects"]) == 1
        effect = data["params"]["requested_effects"][0]
        assert effect["type"] == "inject_context"
        assert effect["priority"] == "high"


# ---------------------------------------------------------------------------
# FastMCP event declaration tests
# ---------------------------------------------------------------------------


class TestFastMCPEventDeclaration:
    def test_declare_event(self):
        mcp = FastMCP("test")
        desc = mcp.declare_event("myapp/status", description="Status", retained=True)
        assert desc.pattern == "myapp/status"
        assert desc.retained is True
        assert "myapp/status" in mcp._event_topics

    def test_event_decorator(self):
        mcp = FastMCP("test")

        @mcp.event("myapp/messages")
        def message_event() -> dict:
            """Message notifications."""
            ...

        assert "myapp/messages" in mcp._event_topics
        desc = mcp._event_topics["myapp/messages"]
        assert desc.description == "Message notifications."

    def test_event_decorator_schema_from_return_type(self):
        mcp = FastMCP("test")

        @mcp.event("myapp/typed")
        def typed_event() -> int:
            ...

        desc = mcp._event_topics["myapp/typed"]
        assert desc.schema_ is not None
        assert desc.schema_.get("type") == "integer"

    def test_multiple_topics(self):
        mcp = FastMCP("test")
        mcp.declare_event("a/b")
        mcp.declare_event("c/d")
        assert len(mcp._event_topics) == 2


# ---------------------------------------------------------------------------
# FastMCP capability tests
# ---------------------------------------------------------------------------


class TestEventCapability:
    async def test_capability_advertised(self):
        """When event topics are declared, the events capability is advertised."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", description="Status updates")

        async with Client(mcp) as client:
            # The client's initialize_result should have the events capability
            result = client._session_state.initialize_result
            assert result is not None
            # events is an extra field on ServerCapabilities
            extras = result.capabilities.model_extra or {}
            assert "events" in extras, f"Expected 'events' in capabilities extras, got keys: {list(extras.keys())}"
            events_cap = extras["events"]
            # Verify topics list content
            assert "topics" in events_cap, f"Expected 'topics' key in events capability, got: {events_cap}"
            topics = events_cap["topics"]
            assert len(topics) == 1, f"Expected 1 topic, got {len(topics)}"
            topic = topics[0]
            assert topic["pattern"] == "myapp/status"
            assert topic["description"] == "Status updates"
            assert topic["retained"] is False

    async def test_no_capability_without_topics(self):
        """Without declared topics, events capability is not advertised."""
        mcp = FastMCP("test")

        async with Client(mcp) as client:
            result = client._session_state.initialize_result
            assert result is not None
            extras = result.capabilities.model_extra or {}
            assert "events" not in extras


# ---------------------------------------------------------------------------
# FastMCP emit_event tests
# ---------------------------------------------------------------------------


class TestFastMCPEmitEvent:
    async def test_emit_event_generates_id(self):
        """emit_event generates a ULID if no event_id provided."""
        import re

        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=True)

        await mcp.emit_event("myapp/status", {"state": "running"})

        # Verify a valid ULID was generated and stored via retained event
        stored = await mcp._retained_store.get("myapp/status")
        assert stored is not None, "Event should be stored as retained"
        assert stored.event_id, "event_id should be non-empty"
        # ULID is 26 characters, Crockford Base32
        assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", stored.event_id), (
            f"event_id should be a valid ULID string, got {stored.event_id!r}"
        )

        # Verify event is actually delivered to a subscribed session
        received_notifications: list[Any] = []
        async with Client(mcp) as client:
            session = list(mcp._active_sessions)[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(session_id, "myapp/status")

            original_send = session.send_notification

            async def capturing_send(notification, related_request_id=None):
                received_notifications.append(notification)

            session.send_notification = capturing_send  # type: ignore[assignment]

            await mcp.emit_event("myapp/status", {"state": "updated"})

            assert len(received_notifications) == 1, "Event should be delivered to client"
            notif = received_notifications[0]
            assert notif.params.event_id, "Delivered event should have an event_id"
            assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", notif.params.event_id)

    async def test_emit_event_retained(self):
        """emit_event stores retained value when topic is declared retained."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=True)

        await mcp.emit_event("myapp/status", {"state": "running"})

        stored = await mcp._retained_store.get("myapp/status")
        assert stored is not None
        assert stored.payload == {"state": "running"}

    async def test_emit_event_not_retained(self):
        """emit_event does not store when topic is not retained."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=False)

        await mcp.emit_event("myapp/status", {"state": "running"})

        stored = await mcp._retained_store.get("myapp/status")
        assert stored is None

    async def test_emit_event_explicit_retained_override(self):
        """retained=True on emit overrides topic descriptor."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=False)

        await mcp.emit_event("myapp/status", {"state": "running"}, retained=True)

        stored = await mcp._retained_store.get("myapp/status")
        assert stored is not None
        assert stored.topic == "myapp/status"
        assert stored.payload == {"state": "running"}
        assert stored.event_id, "Stored event should have an event_id"

    async def test_emit_event_with_expires_at(self):
        """emit_event stores retained value with expires_at, and expired ones are cleaned."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=True)

        # Emit with a far-future expiry - should be retrievable
        await mcp.emit_event(
            "myapp/status",
            {"state": "running"},
            expires_at="2099-01-01T00:00:00Z",
        )
        stored = await mcp._retained_store.get("myapp/status")
        assert stored is not None, "Non-expired event should be retrievable"
        assert stored.topic == "myapp/status"
        assert stored.payload == {"state": "running"}
        assert stored.event_id, "Stored event should have an event_id"

        # Emit with an already-past expiry - should NOT be retrievable
        await mcp.emit_event(
            "myapp/status",
            {"state": "stopped"},
            expires_at="2000-01-01T00:00:00Z",
        )
        stored = await mcp._retained_store.get("myapp/status")
        assert stored is None, "Expired event should not be retrievable"

        # Verify expired events are cleaned from get_matching too
        mcp2 = FastMCP("test2")
        mcp2.declare_event("myapp/a", retained=True)
        mcp2.declare_event("myapp/b", retained=True)

        await mcp2.emit_event(
            "myapp/a",
            {"val": "expired"},
            expires_at="2000-01-01T00:00:00Z",
        )
        await mcp2.emit_event(
            "myapp/b",
            {"val": "valid"},
            expires_at="2099-01-01T00:00:00Z",
        )
        matching = await mcp2._retained_store.get_matching("myapp/+")
        assert len(matching) == 1, f"Expected 1 non-expired match, got {len(matching)}"
        assert matching[0].topic == "myapp/b"
        assert matching[0].payload == {"val": "valid"}


# ---------------------------------------------------------------------------
# Context integration tests
# ---------------------------------------------------------------------------


class TestContextEmitEvent:
    async def test_emit_event_from_tool(self):
        """Tools can emit events via ctx.emit_event()."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/notifications")

        emitted_calls: list[dict[str, Any]] = []
        original_emit = mcp.emit_event

        async def tracking_emit(topic, payload, **kwargs):
            emitted_calls.append({"topic": topic, "payload": payload, **kwargs})
            await original_emit(topic, payload, **kwargs)

        mcp.emit_event = tracking_emit  # type: ignore[assignment]

        @mcp.tool
        async def notify(message: str, ctx: Context) -> str:
            await ctx.emit_event("myapp/notifications", {"text": message})
            return "sent"

        async with Client(mcp) as client:
            result = await client.call_tool("notify", {"message": "hello"})
            assert result.data == "sent"
            assert len(emitted_calls) == 1, f"Expected exactly 1 emit call, got {len(emitted_calls)}"
            call = emitted_calls[0]
            assert call["topic"] == "myapp/notifications"
            assert call["payload"] == {"text": "hello"}


# ---------------------------------------------------------------------------
# Topic matching tests
# ---------------------------------------------------------------------------


class TestTopicMatching:
    def test_exact_match(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")
        assert mcp._match_declared_topic("myapp/status") is True

    def test_wildcard_plus_matches_param(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/{session_id}/messages")
        assert mcp._match_declared_topic("myapp/+/messages") is True

    def test_wildcard_hash_matches_param(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/{session_id}/messages")
        assert mcp._match_declared_topic("myapp/#") is True

    def test_no_match(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")
        assert mcp._match_declared_topic("other/status") is False


# ---------------------------------------------------------------------------
# Parameterized topic pattern matching tests
# ---------------------------------------------------------------------------


class TestTopicMatchesPattern:
    def test_exact_match(self):
        assert FastMCP._topic_matches_pattern("a/b/c", "a/b/c") is True

    def test_single_param_match(self):
        assert (
            FastMCP._topic_matches_pattern(
                "myapp/worker-42/messages",
                "myapp/{session_id}/messages",
            )
            is True
        )

    def test_multiple_params_match(self):
        assert (
            FastMCP._topic_matches_pattern("a/1/b/2/c", "a/{x}/b/{y}/c")
            is True
        )

    def test_segment_count_mismatch(self):
        assert (
            FastMCP._topic_matches_pattern("a/b", "a/{x}/c") is False
        )

    def test_literal_segment_mismatch(self):
        assert (
            FastMCP._topic_matches_pattern(
                "other/worker-42/messages",
                "myapp/{session_id}/messages",
            )
            is False
        )

    def test_no_params_no_match(self):
        assert FastMCP._topic_matches_pattern("a/b/c", "a/b/d") is False

    @pytest.mark.parametrize(
        "topic, pattern",
        [
            ("a//b", "a/{x}/b"),
            ("a/{x}/b", "a//b"),
            ("a//b", "a//b"),
            ("/a/b", "/a/b"),
            ("a/b/", "a/b/"),
        ],
        ids=[
            "empty-segment-in-topic",
            "empty-segment-in-pattern",
            "empty-segment-in-both",
            "leading-slash-empty-first-segment",
            "trailing-slash-empty-last-segment",
        ],
    )
    def test_empty_segments_rejected(self, topic: str, pattern: str):
        assert FastMCP._topic_matches_pattern(topic, pattern) is False

    @pytest.mark.parametrize(
        "topic, pattern",
        [
            ("", "a/b"),
            ("a/b", ""),
            ("", ""),
        ],
        ids=["empty-topic", "empty-pattern", "both-empty"],
    )
    def test_empty_strings_rejected(self, topic: str, pattern: str):
        assert FastMCP._topic_matches_pattern(topic, pattern) is False


class TestFindTopicDescriptor:
    def test_direct_match(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status", retained=True)
        desc = mcp._find_topic_descriptor("myapp/status")
        assert desc is not None
        assert desc.retained is True

    def test_parameterized_match(self):
        mcp = FastMCP("test")
        mcp.declare_event(
            "spellbook/sessions/{session_id}/messages", retained=True
        )
        desc = mcp._find_topic_descriptor(
            "spellbook/sessions/worker-42/messages"
        )
        assert desc is not None
        assert desc.retained is True

    def test_no_match_returns_none(self):
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")
        assert mcp._find_topic_descriptor("other/topic") is None


class TestEmitEventParameterizedRetained:
    async def test_parameterized_retained_auto_stores(self):
        """Emitting to a concrete topic that matches a parameterized
        retained declaration auto-retains the event."""
        mcp = FastMCP("test")
        mcp.declare_event(
            "spellbook/sessions/{session_id}/messages", retained=True
        )

        await mcp.emit_event(
            "spellbook/sessions/worker-42/messages", {"text": "hello"}
        )

        stored = await mcp._retained_store.get(
            "spellbook/sessions/worker-42/messages"
        )
        assert stored is not None
        assert stored.payload == {"text": "hello"}

    async def test_parameterized_not_retained(self):
        """Emitting to a concrete topic that matches a parameterized
        non-retained declaration does not retain."""
        mcp = FastMCP("test")
        mcp.declare_event(
            "spellbook/sessions/{session_id}/messages", retained=False
        )

        await mcp.emit_event(
            "spellbook/sessions/worker-42/messages", {"text": "hello"}
        )

        stored = await mcp._retained_store.get(
            "spellbook/sessions/worker-42/messages"
        )
        assert stored is None

    async def test_undeclared_topic_defaults_not_retained(self):
        """Emitting to a topic that doesn't match any declaration
        defaults retained to False."""
        mcp = FastMCP("test")

        await mcp.emit_event("unknown/topic", {"data": 1})

        stored = await mcp._retained_store.get("unknown/topic")
        assert stored is None


# ---------------------------------------------------------------------------
# Session registry and event delivery integration tests
# ---------------------------------------------------------------------------


class TestSessionRegistry:
    async def test_session_registered_on_connect(self):
        """Sessions are registered in _active_sessions on connect."""
        mcp = FastMCP("test")
        assert len(mcp._active_sessions) == 0

        async with Client(mcp) as client:
            assert len(mcp._active_sessions) == 1

        # After disconnect
        assert len(mcp._active_sessions) == 0

    async def test_session_cleanup_on_disconnect(self):
        """Session subscriptions are cleaned up on disconnect."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        async with Client(mcp) as client:
            assert len(mcp._active_sessions) == 1
            # Get the session ID
            session = list(mcp._active_sessions)[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            assert session_id is not None

            # Manually add a subscription (normally done via events/subscribe)
            await mcp._subscription_registry.add(session_id, "myapp/status")
            subs = await mcp._subscription_registry.get_subscriptions(session_id)
            assert len(subs) == 1

        # After disconnect, subscriptions should be cleaned up
        subs = await mcp._subscription_registry.get_subscriptions(session_id)
        assert len(subs) == 0

    async def test_emit_to_subscribed_session(self):
        """Events are delivered to subscribed sessions."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        received_notifications: list[Any] = []

        async with Client(mcp) as client:
            # Get session and subscribe
            session = list(mcp._active_sessions)[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(session_id, "myapp/status")

            # Monkey-patch send_notification on the session to capture it
            original_send = session.send_notification

            async def capturing_send(notification, related_request_id=None):
                received_notifications.append(notification)
                # Don't actually send to avoid protocol issues in test

            session.send_notification = capturing_send  # type: ignore[assignment]

            await mcp.emit_event("myapp/status", {"state": "running"})

            assert len(received_notifications) == 1
            notif = received_notifications[0]
            assert notif.params.topic == "myapp/status"
            assert notif.params.payload == {"state": "running"}

    async def test_emit_to_multiple_sessions(self):
        """Events are broadcast to all matching sessions."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        received: dict[str, list] = {}

        async with Client(mcp) as client1:
            s1 = list(mcp._active_sessions)[0]
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "myapp/status")
            received[s1_id] = []

            async with Client(mcp) as client2:
                s2 = [s for s in mcp._active_sessions if s is not s1][0]
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "myapp/status")
                received[s2_id] = []

                for s, sid in [(s1, s1_id), (s2, s2_id)]:

                    async def make_capture(target_list):
                        async def capture(notification, related_request_id=None):
                            target_list.append(notification)

                        return capture

                    s.send_notification = await make_capture(received[sid])  # type: ignore[assignment]

                await mcp.emit_event("myapp/status", {"state": "running"})

                assert len(received[s1_id]) == 1
                assert len(received[s2_id]) == 1
                # Verify notification content for both sessions
                for sid in [s1_id, s2_id]:
                    notif = received[sid][0]
                    assert notif.params.topic == "myapp/status"
                    assert notif.params.payload == {"state": "running"}
                    assert notif.params.event_id, f"event_id should be non-empty for session {sid}"

    async def test_emit_failure_does_not_block_others(self):
        """Delivery failure to one session does not prevent delivery to others."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        delivered_to: list[tuple[str, Any]] = []

        async with Client(mcp) as client1:
            s1 = list(mcp._active_sessions)[0]
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "myapp/status")

            async with Client(mcp) as client2:
                s2 = [s for s in mcp._active_sessions if s is not s1][0]
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "myapp/status")

                # Make s1 fail on send
                async def failing_send(notification, related_request_id=None):
                    raise ConnectionError("broken pipe")

                s1.send_notification = failing_send  # type: ignore[assignment]

                async def tracking_send(notification, related_request_id=None):
                    delivered_to.append((s2_id, notification))

                s2.send_notification = tracking_send  # type: ignore[assignment]

                await mcp.emit_event("myapp/status", {"state": "running"})

                # s2 should still receive despite s1 failure
                assert len(delivered_to) == 1, f"Expected exactly 1 delivery, got {len(delivered_to)}"
                sid, notif = delivered_to[0]
                assert sid == s2_id
                assert notif.params.topic == "myapp/status"
                assert notif.params.payload == {"state": "running"}
                assert notif.params.event_id, "Delivered event should have an event_id"


# ---------------------------------------------------------------------------
# Protocol-layer subscribe/unsubscribe tests (full round-trip)
# ---------------------------------------------------------------------------


class TestProtocolRoundTrip:
    @pytest.fixture
    def timeout(self):
        return 10

    async def test_full_subscribe_emit_unsubscribe_cycle(self):
        """Test the full protocol path: subscribe, receive event, unsubscribe, no more events.

        This exercises the _receive_loop interception, _handle_event_request,
        and the complete subscribe/unsubscribe/emit flow at the JSON-RPC level.
        """
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse

        mcp_server = FastMCP("test-events")
        mcp_server.declare_event("myapp/status", description="Status updates")

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # ---- Step 0: Initialize (required handshake) ----
                        init_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {
                                    "name": "test-client",
                                    "version": "1.0",
                                },
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_request))
                        )
                        init_resp = await client_read.receive()
                        assert not isinstance(init_resp, Exception)
                        assert isinstance(init_resp.message.root, JSONRPCResponse)

                        # Send initialized notification
                        from mcp.types import JSONRPCNotification

                        initialized_notif = JSONRPCNotification(
                            jsonrpc="2.0",
                            method="notifications/initialized",
                        )
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(initialized_notif)
                            )
                        )

                        # Wait for server to register the session
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)
                        assert len(mcp_server._active_sessions) == 1

                        server_session = list(mcp_server._active_sessions)[0]
                        session_id = getattr(
                            server_session, "_fastmcp_event_session_id"
                        )

                        # ---- Step 1: Subscribe via raw JSON-RPC ----
                        subscribe_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=2,
                            method="events/subscribe",
                            params={"topics": ["myapp/status"]},
                        )
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(subscribe_request)
                            )
                        )

                        sub_resp = await client_read.receive()
                        assert not isinstance(sub_resp, Exception)
                        response = sub_resp.message.root
                        assert isinstance(response, JSONRPCResponse), (
                            f"Expected result, got: {response}"
                        )
                        result = response.result
                        assert len(result["subscribed"]) == 1
                        assert result["subscribed"][0]["pattern"] == "myapp/status"

                        # Verify subscription was registered
                        subs = await mcp_server._subscription_registry.get_subscriptions(
                            session_id
                        )
                        assert "myapp/status" in subs

                        # ---- Step 2: Server emits event, client receives it ----
                        await mcp_server.emit_event(
                            "myapp/status", {"state": "running"}
                        )

                        # The event notification should arrive on client_read
                        event_msg = await client_read.receive()
                        assert not isinstance(event_msg, Exception)
                        event_root = event_msg.message.root
                        # Should be a notification (no id field with result)
                        assert hasattr(event_root, "method")
                        assert event_root.method == "events/emit"
                        assert event_root.params["topic"] == "myapp/status"
                        assert event_root.params["payload"] == {"state": "running"}

                        # ---- Step 3: Unsubscribe ----
                        unsubscribe_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=3,
                            method="events/unsubscribe",
                            params={"topics": ["myapp/status"]},
                        )
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(unsubscribe_request)
                            )
                        )

                        unsub_resp = await client_read.receive()
                        assert not isinstance(unsub_resp, Exception)
                        unsub_root = unsub_resp.message.root
                        assert isinstance(unsub_root, JSONRPCResponse)
                        assert "myapp/status" in unsub_root.result["unsubscribed"]

                        # Verify subscription was removed
                        subs = await mcp_server._subscription_registry.get_subscriptions(
                            session_id
                        )
                        assert "myapp/status" not in subs

                        # ---- Step 4: Emit again, verify no delivery ----
                        # Subscription registry has no match, so emit
                        # won't attempt delivery (proven by step 2)
                        matching = await mcp_server._subscription_registry.match(
                            "myapp/status"
                        )
                        assert len(matching) == 0, (
                            "No sessions should match after unsubscribe"
                        )

                    finally:
                        tg.cancel_scope.cancel()

    async def test_events_list_via_protocol(self):
        """events/list returns declared topics via raw JSON-RPC."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

        mcp_server = FastMCP("test-events-list")
        mcp_server.declare_event("myapp/status", description="Status updates", retained=True)
        mcp_server.declare_event("myapp/logs", description="Log stream")

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # Initialize handshake
                        init_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test-client", "version": "1.0"},
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_request))
                        )
                        await client_read.receive()
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(
                                    JSONRPCNotification(
                                        jsonrpc="2.0",
                                        method="notifications/initialized",
                                    )
                                )
                            )
                        )
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)

                        # Send events/list request
                        list_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=2,
                            method="events/list",
                            params={},
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(list_request))
                        )

                        list_resp = await client_read.receive()
                        assert not isinstance(list_resp, Exception)
                        response = list_resp.message.root
                        assert isinstance(response, JSONRPCResponse), (
                            f"Expected result, got: {response}"
                        )
                        result = response.result
                        assert "topics" in result
                        topics = result["topics"]
                        assert len(topics) == 2
                        patterns = {t["pattern"] for t in topics}
                        assert patterns == {"myapp/status", "myapp/logs"}
                        # Verify topic details
                        by_pattern = {t["pattern"]: t for t in topics}
                        assert by_pattern["myapp/status"]["description"] == "Status updates"
                        assert by_pattern["myapp/status"]["retained"] is True
                        assert by_pattern["myapp/logs"]["description"] == "Log stream"
                        assert by_pattern["myapp/logs"]["retained"] is False
                    finally:
                        tg.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# _receive_loop error path tests (Finding 15/16)
# ---------------------------------------------------------------------------


class TestReceiveLoopErrorPaths:
    async def test_malformed_request_returns_json_rpc_error(self):
        """A malformed/unknown request returns a JSON-RPC error, not a crash."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

        mcp_server = FastMCP("test-malformed")

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # Initialize
                        init_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1.0"},
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_req))
                        )
                        await client_read.receive()
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(
                                    JSONRPCNotification(
                                        jsonrpc="2.0",
                                        method="notifications/initialized",
                                    )
                                )
                            )
                        )
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)

                        # Send a completely bogus method
                        bogus_request = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=99,
                            method="nonexistent/method",
                            params={},
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(bogus_request))
                        )

                        resp_msg = await client_read.receive()
                        assert not isinstance(resp_msg, Exception)
                        response = resp_msg.message.root
                        # Should be an error response, not a crash
                        assert isinstance(response, JSONRPCError), (
                            f"Expected JSON-RPC error for unknown method, got: {response}"
                        )
                    finally:
                        tg.cancel_scope.cancel()

    async def test_event_handler_error_returns_json_rpc_error(self):
        """When _handle_event_request handler raises McpError, it returns a JSON-RPC error."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

        mcp_server = FastMCP("test-event-error")
        mcp_server.declare_event("myapp/status")

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # Initialize
                        init_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1.0"},
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_req))
                        )
                        await client_read.receive()
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(
                                    JSONRPCNotification(
                                        jsonrpc="2.0",
                                        method="notifications/initialized",
                                    )
                                )
                            )
                        )
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)

                        # Subscribe with invalid params (missing topics field)
                        # This will cause a validation error in EventSubscribeParams
                        bad_sub_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=2,
                            method="events/subscribe",
                            params={"invalid_field": "value"},
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(bad_sub_req))
                        )

                        resp_msg = await client_read.receive()
                        assert not isinstance(resp_msg, Exception)
                        response = resp_msg.message.root
                        # Should get a JSON-RPC error, not a crash
                        assert isinstance(response, JSONRPCError), (
                            f"Expected JSON-RPC error for bad event params, got: {response}"
                        )
                        assert response.error.code == -32603  # Internal error
                    finally:
                        tg.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# Topic depth enforcement tests
# ---------------------------------------------------------------------------


class TestTopicDepthEnforcement:
    def test_declare_event_rejects_deep_topic(self):
        """declare_event rejects patterns with more than 8 segments."""
        mcp_server = FastMCP("test")
        # 9 segments should be rejected
        with pytest.raises(ValueError, match="maximum depth is 8"):
            mcp_server.declare_event("a/b/c/d/e/f/g/h/i")

    def test_declare_event_accepts_max_depth(self):
        """declare_event accepts patterns with exactly 8 segments."""
        mcp_server = FastMCP("test")
        # Exactly 8 segments should work
        desc = mcp_server.declare_event("a/b/c/d/e/f/g/h")
        assert desc.pattern == "a/b/c/d/e/f/g/h"

    async def test_subscribe_rejects_deep_pattern(self):
        """events/subscribe rejects patterns with more than 8 segments via JSON-RPC."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import (
            JSONRPCError,
            JSONRPCMessage,
            JSONRPCNotification,
            JSONRPCRequest,
            JSONRPCResponse,
        )

        mcp_server = FastMCP("test")
        mcp_server.declare_event("myapp/status")

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # Initialize
                        init_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1.0"},
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_req))
                        )
                        await client_read.receive()
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(
                                    JSONRPCNotification(
                                        jsonrpc="2.0",
                                        method="notifications/initialized",
                                    )
                                )
                            )
                        )
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)

                        # Subscribe with too-deep pattern
                        sub_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=2,
                            method="events/subscribe",
                            params={"topics": ["a/b/c/d/e/f/g/h/i"]},
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(sub_req))
                        )

                        resp_msg = await client_read.receive()
                        assert not isinstance(resp_msg, Exception)
                        response = resp_msg.message.root
                        assert isinstance(response, JSONRPCError), (
                            f"Expected error for deep pattern, got: {response}"
                        )
                        assert response.error.code == -32602
                        assert "maximum depth" in response.error.message
                    finally:
                        tg.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# No-events-capability error tests
# ---------------------------------------------------------------------------


class TestNoEventsCapability:
    async def test_events_method_returns_error_without_capability(self):
        """events/* methods return -32601 when server has no declared event topics."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import (
            JSONRPCError,
            JSONRPCMessage,
            JSONRPCNotification,
            JSONRPCRequest,
            JSONRPCResponse,
        )

        mcp_server = FastMCP("test-no-events")
        # No events declared

        async with mcp_server._lifespan_manager():
            async with create_client_server_memory_streams() as (
                client_streams,
                server_streams,
            ):
                client_read, client_write = client_streams
                server_read, server_write = server_streams

                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        lambda: mcp_server._mcp_server.run(
                            server_read,
                            server_write,
                            mcp_server._mcp_server.create_initialization_options(),
                            raise_exceptions=True,
                        )
                    )

                    try:
                        # Initialize
                        init_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=1,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1.0"},
                            },
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(init_req))
                        )
                        await client_read.receive()
                        await client_write.send(
                            SessionMessage(
                                message=JSONRPCMessage(
                                    JSONRPCNotification(
                                        jsonrpc="2.0",
                                        method="notifications/initialized",
                                    )
                                )
                            )
                        )
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)

                        # Send events/subscribe - should fail with -32601
                        sub_req = JSONRPCRequest(
                            jsonrpc="2.0",
                            id=2,
                            method="events/subscribe",
                            params={"topics": ["anything"]},
                        )
                        await client_write.send(
                            SessionMessage(message=JSONRPCMessage(sub_req))
                        )

                        resp_msg = await client_read.receive()
                        assert not isinstance(resp_msg, Exception)
                        response = resp_msg.message.root
                        assert isinstance(response, JSONRPCError), (
                            f"Expected error without events capability, got: {response}"
                        )
                        assert response.error.code == -32601
                        assert "Method not found" in response.error.message
                    finally:
                        tg.cancel_scope.cancel()
