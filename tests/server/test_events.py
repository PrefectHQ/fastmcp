"""Tests for application-level MCP events support (Phase 2).

Tests event declaration, emission, subscription, retained values,
session registry, context integration, and capabilities.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import EventSubscribeRequest, ServerNotification

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.events import (
    EventEffect,
    EventEmitNotification,
    EventParams,
    EventSubscribeParams,
    EventSubscribeResult,
    EventTopicDescriptor,
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
        event = RetainedEvent(topic="t", eventId="e1", payload={"x": 1})
        await store.set("t", event)
        assert await store.get("t") == event

    async def test_get_nonexistent(self):
        store = RetainedValueStore()
        assert await store.get("nonexistent") is None

    async def test_get_matching(self):
        store = RetainedValueStore()
        await store.set(
            "myapp/a", RetainedEvent(topic="myapp/a", eventId="e1", payload=1)
        )
        await store.set(
            "myapp/b", RetainedEvent(topic="myapp/b", eventId="e2", payload=2)
        )
        await store.set(
            "other/c", RetainedEvent(topic="other/c", eventId="e3", payload=3)
        )
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
        event = RetainedEvent(topic="t", eventId="e1", payload=1)
        await store.set("t", event)
        await store.delete("t")
        assert await store.get("t") is None

    async def test_expiry(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", eventId="e1", payload=1)
        # Set with expired timestamp
        await store.set("t", event, expires_at="2000-01-01T00:00:00Z")
        assert await store.get("t") is None

    async def test_not_expired(self):
        store = RetainedValueStore()
        event = RetainedEvent(topic="t", eventId="e1", payload=1)
        await store.set("t", event, expires_at="2099-01-01T00:00:00Z")
        assert await store.get("t") == event

    async def test_get_matching_skips_expired(self):
        store = RetainedValueStore()
        await store.set(
            "myapp/a",
            RetainedEvent(topic="myapp/a", eventId="e1", payload=1),
            expires_at="2000-01-01T00:00:00Z",
        )
        await store.set(
            "myapp/b",
            RetainedEvent(topic="myapp/b", eventId="e2", payload=2),
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
                eventId="e1",
                payload={"status": "running"},
            )
        )
        assert notification.method == "events/emit"
        assert notification.params.topic == "myapp/status"

    def test_serialization(self):
        notification = EventEmitNotification(
            params=EventParams(
                topic="myapp/status",
                eventId="e1",
                payload={"status": "running"},
                retained=True,
                requestedEffects=[EventEffect(type="inject_context", priority="high")],
            )
        )
        data = notification.model_dump(exclude_none=True)
        assert data["method"] == "events/emit"
        assert data["params"]["topic"] == "myapp/status"
        assert data["params"]["eventId"] == "e1"
        assert data["params"]["payload"] == {"status": "running"}
        assert data["params"]["retained"] is True
        assert len(data["params"]["requestedEffects"]) == 1
        effect = data["params"]["requestedEffects"][0]
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
            return {}

        assert "myapp/messages" in mcp._event_topics
        desc = mcp._event_topics["myapp/messages"]
        assert desc.description == "Message notifications."

    def test_event_decorator_schema_from_return_type(self):
        mcp = FastMCP("test")

        @mcp.event("myapp/typed")
        def typed_event() -> int:
            return 0

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
            # events is a first-class field on ServerCapabilities
            events_cap = result.capabilities.events
            assert events_cap is not None, "Expected events capability to be set"
            assert len(events_cap.topics) == 1, (
                f"Expected 1 topic, got {len(events_cap.topics)}"
            )
            topic = events_cap.topics[0]
            assert topic.pattern == "myapp/status"
            assert topic.description == "Status updates"
            assert topic.retained is False

    async def test_no_capability_without_topics(self):
        """Without declared topics, events capability is not advertised."""
        mcp = FastMCP("test")

        async with Client(mcp) as client:
            result = client._session_state.initialize_result
            assert result is not None
            assert result.capabilities.events is None


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
        async with Client(mcp) as _client:
            session = list(mcp._active_sessions.values())[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(session_id, "myapp/status")

            _original_send = session.send_notification

            async def capturing_send(
                notification: ServerNotification,
                related_request_id: str | int | None = None,
            ) -> None:
                received_notifications.append(notification)

            setattr(session, "send_notification", capturing_send)

            await mcp.emit_event("myapp/status", {"state": "updated"})

            assert len(received_notifications) == 1, (
                "Event should be delivered to client"
            )
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

        async def tracking_emit(
            topic: str,
            payload: Any,
            *,
            event_id: str | None = None,
            retained: bool | None = None,
            source: str | None = None,
            correlation_id: str | None = None,
            requested_effects: list[EventEffect] | None = None,
            expires_at: str | None = None,
            target_session_ids: Any = None,
        ) -> None:
            kwargs: dict[str, Any] = {}
            if event_id is not None:
                kwargs["event_id"] = event_id
            if retained is not None:
                kwargs["retained"] = retained
            if source is not None:
                kwargs["source"] = source
            if correlation_id is not None:
                kwargs["correlation_id"] = correlation_id
            if requested_effects is not None:
                kwargs["requested_effects"] = requested_effects
            if expires_at is not None:
                kwargs["expires_at"] = expires_at
            if target_session_ids is not None:
                kwargs["target_session_ids"] = target_session_ids
            emitted_calls.append({"topic": topic, "payload": payload, **kwargs})
            await original_emit(topic, payload, **kwargs)

        setattr(mcp, "emit_event", tracking_emit)

        @mcp.tool
        async def notify(message: str, ctx: Context) -> str:
            await ctx.emit_event("myapp/notifications", {"text": message})
            return "sent"

        async with Client(mcp) as client:
            result = await client.call_tool("notify", {"message": "hello"})
            assert result.data == "sent"
            assert len(emitted_calls) == 1, (
                f"Expected exactly 1 emit call, got {len(emitted_calls)}"
            )
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
        assert FastMCP._topic_matches_pattern("a/1/b/2/c", "a/{x}/b/{y}/c") is True

    def test_segment_count_mismatch(self):
        assert FastMCP._topic_matches_pattern("a/b", "a/{x}/c") is False

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
        mcp.declare_event("spellbook/sessions/{session_id}/messages", retained=True)
        desc = mcp._find_topic_descriptor("spellbook/sessions/worker-42/messages")
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
        mcp.declare_event("spellbook/sessions/{session_id}/messages", retained=True)

        await mcp.emit_event("spellbook/sessions/worker-42/messages", {"text": "hello"})

        stored = await mcp._retained_store.get("spellbook/sessions/worker-42/messages")
        assert stored is not None
        assert stored.payload == {"text": "hello"}

    async def test_parameterized_not_retained(self):
        """Emitting to a concrete topic that matches a parameterized
        non-retained declaration does not retain."""
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/sessions/{session_id}/messages", retained=False)

        await mcp.emit_event("spellbook/sessions/worker-42/messages", {"text": "hello"})

        stored = await mcp._retained_store.get("spellbook/sessions/worker-42/messages")
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

        async with Client(mcp) as _client:
            assert len(mcp._active_sessions) == 1

        # After disconnect
        assert len(mcp._active_sessions) == 0

    async def test_session_cleanup_on_disconnect(self):
        """Session subscriptions are cleaned up on disconnect."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        async with Client(mcp) as _client:
            assert len(mcp._active_sessions) == 1
            # Get the session ID
            session = list(mcp._active_sessions.values())[0]
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

        async with Client(mcp) as _client:
            # Get session and subscribe
            session = list(mcp._active_sessions.values())[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(session_id, "myapp/status")

            # Monkey-patch send_notification on the session to capture it
            _original_send = session.send_notification

            async def capturing_send(
                notification: ServerNotification,
                related_request_id: str | int | None = None,
            ) -> None:
                received_notifications.append(notification)
                # Don't actually send to avoid protocol issues in test

            setattr(session, "send_notification", capturing_send)

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

        async with Client(mcp) as _client1:
            s1 = list(mcp._active_sessions.values())[0]
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "myapp/status")
            received[s1_id] = []

            async with Client(mcp) as _client2:
                s2 = [s for s in mcp._active_sessions.values() if s is not s1][0]
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "myapp/status")
                received[s2_id] = []

                for s, sid in [(s1, s1_id), (s2, s2_id)]:

                    async def make_capture(target_list: list[Any]) -> Any:
                        async def capture(
                            notification: ServerNotification,
                            related_request_id: str | int | None = None,
                        ) -> None:
                            target_list.append(notification)

                        return capture

                    setattr(s, "send_notification", await make_capture(received[sid]))

                await mcp.emit_event("myapp/status", {"state": "running"})

                assert len(received[s1_id]) == 1
                assert len(received[s2_id]) == 1
                # Verify notification content for both sessions
                for sid in [s1_id, s2_id]:
                    notif = received[sid][0]
                    assert notif.params.topic == "myapp/status"
                    assert notif.params.payload == {"state": "running"}
                    assert notif.params.event_id, (
                        f"event_id should be non-empty for session {sid}"
                    )

    async def test_emit_failure_does_not_block_others(self):
        """Delivery failure to one session does not prevent delivery to others."""
        mcp = FastMCP("test")
        mcp.declare_event("myapp/status")

        delivered_to: list[tuple[str, Any]] = []

        async with Client(mcp) as _client1:
            s1 = list(mcp._active_sessions.values())[0]
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "myapp/status")

            async with Client(mcp) as _client2:
                s2 = [s for s in mcp._active_sessions.values() if s is not s1][0]
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "myapp/status")

                # Make s1 fail on send
                async def failing_send(
                    notification: ServerNotification,
                    related_request_id: str | int | None = None,
                ) -> None:
                    raise ConnectionError("broken pipe")

                setattr(s1, "send_notification", failing_send)

                async def tracking_send(
                    notification: ServerNotification,
                    related_request_id: str | int | None = None,
                ) -> None:
                    delivered_to.append((s2_id, notification))

                setattr(s2, "send_notification", tracking_send)

                await mcp.emit_event("myapp/status", {"state": "running"})

                # s2 should still receive despite s1 failure
                assert len(delivered_to) == 1, (
                    f"Expected exactly 1 delivery, got {len(delivered_to)}"
                )
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
                            SessionMessage(message=JSONRPCMessage(initialized_notif))
                        )

                        # Wait for server to register the session
                        for _ in range(50):
                            if mcp_server._active_sessions:
                                break
                            await asyncio.sleep(0.05)
                        assert len(mcp_server._active_sessions) == 1

                        server_session = list(mcp_server._active_sessions.values())[0]
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
                            SessionMessage(message=JSONRPCMessage(subscribe_request))
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
                        subs = (
                            await mcp_server._subscription_registry.get_subscriptions(
                                session_id
                            )
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
                        assert isinstance(event_root, JSONRPCNotification)
                        assert event_root.method == "events/emit"
                        assert event_root.params is not None
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
                            SessionMessage(message=JSONRPCMessage(unsubscribe_request))
                        )

                        unsub_resp = await client_read.receive()
                        assert not isinstance(unsub_resp, Exception)
                        unsub_root = unsub_resp.message.root
                        assert isinstance(unsub_root, JSONRPCResponse)
                        assert "myapp/status" in unsub_root.result["unsubscribed"]

                        # Verify subscription was removed
                        subs = (
                            await mcp_server._subscription_registry.get_subscriptions(
                                session_id
                            )
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
        from mcp.types import (
            JSONRPCMessage,
            JSONRPCNotification,
            JSONRPCRequest,
            JSONRPCResponse,
        )

        mcp_server = FastMCP("test-events-list")
        mcp_server.declare_event(
            "myapp/status", description="Status updates", retained=True
        )
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
                        assert (
                            by_pattern["myapp/status"]["description"]
                            == "Status updates"
                        )
                        assert by_pattern["myapp/status"]["retained"] is True
                        assert by_pattern["myapp/logs"]["description"] == "Log stream"
                        assert by_pattern["myapp/logs"]["retained"] is False
                    finally:
                        tg.cancel_scope.cancel()


# ---------------------------------------------------------------------------
# Error path tests (Finding 15/16)
# ---------------------------------------------------------------------------


class TestErrorPaths:
    async def test_malformed_request_returns_json_rpc_error(self):
        """A malformed/unknown request returns a JSON-RPC error, not a crash."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import (
            JSONRPCError,
            JSONRPCMessage,
            JSONRPCNotification,
            JSONRPCRequest,
        )

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
        """When events/subscribe is sent with invalid params, it returns a JSON-RPC error."""
        import anyio
        from mcp.shared.memory import create_client_server_memory_streams
        from mcp.shared.message import SessionMessage
        from mcp.types import (
            JSONRPCError,
            JSONRPCMessage,
            JSONRPCNotification,
            JSONRPCRequest,
        )

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
                        # The SDK validates the request params before dispatching
                        # to the handler, returning INVALID_PARAMS (-32602)
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
                        assert response.error.code == -32602  # Invalid params
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


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _request_ctx_for_session(session: Any) -> AsyncIterator[None]:
    """Push a minimal RequestContext containing ``session`` for the duration.

    The event subscribe handler reads the session from ``request_ctx``; tests
    that invoke ``_handle_subscribe_events`` directly need this context to be
    populated.
    """
    rctx = RequestContext(
        request_id=0,
        meta=None,
        session=session,
        lifespan_context=None,
        experimental=None,
    )
    token = request_ctx.set(rctx)  # type: ignore[arg-type]
    try:
        yield
    finally:
        request_ctx.reset(token)


async def _subscribe_via_handler(
    mcp: FastMCP, session: Any, topics: list[str]
) -> EventSubscribeResult:
    """Drive ``_handle_subscribe_events`` using the active session.

    Exercises the full authorization path (the same code that the JSON-RPC
    handler runs) without the verbosity of a raw protocol round-trip.
    """
    req = EventSubscribeRequest(params=EventSubscribeParams(topics=topics))
    async with _request_ctx_for_session(session):
        return await mcp._handle_subscribe_events(req)


def _get_active_session(mcp: FastMCP) -> Any:
    """Return the single active session, asserting there is exactly one."""
    sessions = list(mcp._active_sessions.values())
    assert len(sessions) == 1, f"Expected 1 active session, got {len(sessions)}"
    return sessions[0]


# ---------------------------------------------------------------------------
# InitializeResult._meta.session_id exposure
# ---------------------------------------------------------------------------


class TestInitializeResultSessionId:
    async def test_initialize_result_meta_contains_session_id(self):
        """The initialize handshake exposes the server-side session_id via _meta."""
        mcp = FastMCP("test")

        async with Client(mcp) as client:
            init_result = client._session_state.initialize_result
            assert init_result is not None
            assert init_result.meta is not None
            session_id = init_result.meta.get("session_id")  # type: ignore[union-attr]
            assert isinstance(session_id, str) and session_id, (
                "session_id should be a non-empty string"
            )

            server_session = _get_active_session(mcp)
            server_side_id = getattr(server_session, "_fastmcp_event_session_id")
            assert session_id == server_side_id

    async def test_initialize_result_meta_session_id_is_stable_per_session(self):
        """Multiple operations within the same session see a stable id."""
        mcp = FastMCP("test")

        @mcp.tool
        def ping() -> str:
            return "pong"

        async with Client(mcp) as client:
            init_result = client._session_state.initialize_result
            assert init_result is not None
            assert init_result.meta is not None
            session_id_first = init_result.meta.get("session_id")  # type: ignore[union-attr]

            # Issue several operations and confirm the underlying session and
            # its id remain stable.
            await client.call_tool("ping", {})
            await client.call_tool("ping", {})

            server_session = _get_active_session(mcp)
            server_side_id = getattr(server_session, "_fastmcp_event_session_id")
            assert session_id_first == server_side_id

            init_result_after = client._session_state.initialize_result
            assert init_result_after is not None
            assert init_result_after.meta is not None
            assert (
                init_result_after.meta.get("session_id")  # type: ignore[union-attr]
                == session_id_first
            )


# ---------------------------------------------------------------------------
# {session_id} default enforcement (no authorize callback)
# ---------------------------------------------------------------------------


class TestSessionIdEnforcement:
    async def test_can_subscribe_to_own_session_topic(self):
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/sessions/{session_id}/messages")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            sid = getattr(session, "_fastmcp_event_session_id")
            result = await _subscribe_via_handler(
                mcp, session, [f"spellbook/sessions/{sid}/messages"]
            )

        assert len(result.subscribed) == 1
        assert result.subscribed[0].pattern == f"spellbook/sessions/{sid}/messages"
        assert result.rejected == []

    async def test_cannot_subscribe_to_other_session_topic(self):
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/sessions/{session_id}/messages")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp,
                session,
                ["spellbook/sessions/00000000-0000-0000-0000-000000000000/messages"],
            )

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"

    async def test_cannot_use_single_wildcard_in_session_slot(self):
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/sessions/{session_id}/messages")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp, session, ["spellbook/sessions/+/messages"]
            )

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"

    async def test_cannot_use_hash_wildcard_over_session_slot(self):
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/sessions/{session_id}/messages")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp, session, ["spellbook/sessions/#"]
            )

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"

    async def test_public_topic_allows_any_subscriber(self):
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/server/status")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp, session, ["spellbook/server/status"]
            )

        assert len(result.subscribed) == 1
        assert result.rejected == []

    async def test_non_magic_placeholder_allows_wildcard(self):
        """``{project}`` is not magic, so wildcards are permitted in that slot."""
        mcp = FastMCP("test")
        mcp.declare_event("spellbook/builds/{project}/status")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp, session, ["spellbook/builds/+/status"]
            )

        assert len(result.subscribed) == 1
        assert result.rejected == []


# ---------------------------------------------------------------------------
# authorize callback escape hatch
# ---------------------------------------------------------------------------


class TestAuthorizeCallback:
    async def test_authorize_callback_called_with_correct_params(self):
        captured: list[tuple[str, dict[str, str]]] = []

        def authorize(session_id: str, params: dict[str, str]) -> bool:
            captured.append((session_id, params))
            return True

        mcp = FastMCP("test")
        mcp.declare_event("rooms/{room}/chat", authorize=authorize)

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            sid = getattr(session, "_fastmcp_event_session_id")
            result = await _subscribe_via_handler(mcp, session, ["rooms/lobby/chat"])

        assert len(result.subscribed) == 1
        assert captured == [(sid, {"room": "lobby"})]

    async def test_authorize_callback_receives_wildcard_literal(self):
        captured: list[tuple[str, dict[str, str]]] = []

        def authorize(session_id: str, params: dict[str, str]) -> bool:
            captured.append((session_id, params))
            return True

        mcp = FastMCP("test")
        mcp.declare_event("rooms/{room}/chat", authorize=authorize)

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            sid = getattr(session, "_fastmcp_event_session_id")
            result = await _subscribe_via_handler(mcp, session, ["rooms/+/chat"])

        assert len(result.subscribed) == 1
        assert captured == [(sid, {"room": "+"})]

    async def test_authorize_callback_denies_rejects_subscription(self):
        def authorize(session_id: str, params: dict[str, str]) -> bool:
            return False

        mcp = FastMCP("test")
        mcp.declare_event("rooms/{room}/chat", authorize=authorize)

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(mcp, session, ["rooms/lobby/chat"])

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"

    async def test_authorize_callback_exception_fails_closed(
        self, caplog: pytest.LogCaptureFixture
    ):
        def authorize(session_id: str, params: dict[str, str]) -> bool:
            raise RuntimeError("intentional failure for test")

        mcp = FastMCP("test")
        mcp.declare_event("rooms/{room}/chat", authorize=authorize)

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            with caplog.at_level(
                logging.WARNING, logger="fastmcp.server.mixins.mcp_operations"
            ):
                result = await _subscribe_via_handler(
                    mcp, session, ["rooms/lobby/chat"]
                )

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"
        assert any(
            "authorize callback raised" in record.message for record in caplog.records
        ), "Expected a warning log when authorize raises"

    async def test_authorize_callback_overrides_default_session_id_check(self):
        """An authorize callback fully replaces the {session_id} default policy."""

        def authorize(session_id: str, params: dict[str, str]) -> bool:
            return True

        mcp = FastMCP("test")
        mcp.declare_event(
            "sessions/{session_id}/messages", authorize=authorize
        )

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            # Subscribe to a session id that does NOT belong to this session.
            result = await _subscribe_via_handler(
                mcp,
                session,
                ["sessions/00000000-0000-0000-0000-000000000000/messages"],
            )

        assert len(result.subscribed) == 1
        assert result.rejected == []


# ---------------------------------------------------------------------------
# target_session_ids on emit_event
# ---------------------------------------------------------------------------


class TestTargetSessionIds:
    async def _capture_session(
        self, session: Any, sink: list[Any]
    ) -> None:
        async def capturing_send(
            notification: ServerNotification,
            related_request_id: str | int | None = None,
        ) -> None:
            sink.append(notification)

        setattr(session, "send_notification", capturing_send)

    async def test_emit_without_target_session_ids_broadcasts(self):
        """Default behavior: every matching subscriber receives the event."""
        mcp = FastMCP("test")
        mcp.declare_event("public/topic")

        sinks: dict[str, list[Any]] = {}

        async with Client(mcp) as _c1:
            s1 = _get_active_session(mcp)
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "public/topic")
            sinks[s1_id] = []
            await self._capture_session(s1, sinks[s1_id])

            async with Client(mcp) as _c2:
                s2 = next(
                    s for s in mcp._active_sessions.values() if s is not s1
                )
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "public/topic")
                sinks[s2_id] = []
                await self._capture_session(s2, sinks[s2_id])

                async with Client(mcp) as _c3:
                    s3 = next(
                        s
                        for s in mcp._active_sessions.values()
                        if s is not s1 and s is not s2
                    )
                    s3_id = getattr(s3, "_fastmcp_event_session_id")
                    await mcp._subscription_registry.add(s3_id, "public/topic")
                    sinks[s3_id] = []
                    await self._capture_session(s3, sinks[s3_id])

                    await mcp.emit_event("public/topic", {"v": 1})

                    assert len(sinks[s1_id]) == 1
                    assert len(sinks[s2_id]) == 1
                    assert len(sinks[s3_id]) == 1

    async def test_emit_with_target_session_ids_filters(self):
        mcp = FastMCP("test")
        mcp.declare_event("public/topic")

        sinks: dict[str, list[Any]] = {}

        async with Client(mcp) as _c1:
            s1 = _get_active_session(mcp)
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "public/topic")
            sinks[s1_id] = []
            await self._capture_session(s1, sinks[s1_id])

            async with Client(mcp) as _c2:
                s2 = next(
                    s for s in mcp._active_sessions.values() if s is not s1
                )
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "public/topic")
                sinks[s2_id] = []
                await self._capture_session(s2, sinks[s2_id])

                async with Client(mcp) as _c3:
                    s3 = next(
                        s
                        for s in mcp._active_sessions.values()
                        if s is not s1 and s is not s2
                    )
                    s3_id = getattr(s3, "_fastmcp_event_session_id")
                    await mcp._subscription_registry.add(s3_id, "public/topic")
                    sinks[s3_id] = []
                    await self._capture_session(s3, sinks[s3_id])

                    await mcp.emit_event(
                        "public/topic",
                        {"v": 1},
                        target_session_ids=[s1_id, s2_id],
                    )

                    assert len(sinks[s1_id]) == 1
                    assert len(sinks[s2_id]) == 1
                    assert sinks[s3_id] == []

    async def test_emit_with_target_session_ids_intersection_empty_is_noop(self):
        """A target list that overlaps no subscribers delivers nothing, no error."""
        mcp = FastMCP("test")
        mcp.declare_event("public/topic")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            sid = getattr(session, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(sid, "public/topic")
            sink: list[Any] = []
            await self._capture_session(session, sink)

            await mcp.emit_event(
                "public/topic",
                {"v": 1},
                target_session_ids=["nope-not-a-real-session-id"],
            )

            assert sink == []

    async def test_emit_with_target_session_ids_and_subscription_mismatch(self):
        """Targeted session that lacks a matching subscription does not receive."""
        mcp = FastMCP("test")
        mcp.declare_event("public/topic")
        mcp.declare_event("other/topic")

        async with Client(mcp) as _c1:
            s1 = _get_active_session(mcp)
            s1_id = getattr(s1, "_fastmcp_event_session_id")
            await mcp._subscription_registry.add(s1_id, "public/topic")
            sink_s1: list[Any] = []
            await self._capture_session(s1, sink_s1)

            async with Client(mcp) as _c2:
                s2 = next(
                    s for s in mcp._active_sessions.values() if s is not s1
                )
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                # s2 subscribes to a DIFFERENT topic
                await mcp._subscription_registry.add(s2_id, "other/topic")
                sink_s2: list[Any] = []
                await self._capture_session(s2, sink_s2)

                # Target both sessions but emit on a topic only s1 subscribes to.
                await mcp.emit_event(
                    "public/topic",
                    {"v": 1},
                    target_session_ids=[s1_id, s2_id],
                )

                assert len(sink_s1) == 1
                assert sink_s2 == []

    async def test_context_emit_event_supports_target_session_ids(self):
        """Context.emit_event passes target_session_ids through to FastMCP.emit_event."""
        mcp = FastMCP("test")
        mcp.declare_event("public/topic")

        @mcp.tool
        async def fan_out(target: str, ctx: Context) -> str:
            await ctx.emit_event(
                "public/topic", {"hello": "world"}, target_session_ids=[target]
            )
            return "ok"

        async with Client(mcp) as caller:
            # caller is the session that invokes the tool; spin up two
            # additional subscriber sessions.
            caller_session = _get_active_session(mcp)

            async with Client(mcp) as _c2:
                s2 = next(
                    s
                    for s in mcp._active_sessions.values()
                    if s is not caller_session
                )
                s2_id = getattr(s2, "_fastmcp_event_session_id")
                await mcp._subscription_registry.add(s2_id, "public/topic")
                sink_s2: list[Any] = []
                await TestTargetSessionIds()._capture_session(s2, sink_s2)

                async with Client(mcp) as _c3:
                    s3 = next(
                        s
                        for s in mcp._active_sessions.values()
                        if s is not caller_session and s is not s2
                    )
                    s3_id = getattr(s3, "_fastmcp_event_session_id")
                    await mcp._subscription_registry.add(s3_id, "public/topic")
                    sink_s3: list[Any] = []
                    await TestTargetSessionIds()._capture_session(s3, sink_s3)

                    result = await caller.call_tool("fan_out", {"target": s2_id})
                    assert result.data == "ok"

                    assert len(sink_s2) == 1
                    assert sink_s3 == []


# ---------------------------------------------------------------------------
# Wildcard-smuggling regression guards
# ---------------------------------------------------------------------------


class TestWildcardSmuggling:
    async def test_wildcard_smuggling_rejected(self):
        """A subscribe pattern that touches a session-scoped declaration via
        wildcard must be rejected even if it ALSO matches an open declaration.

        Two declarations:
            - ``sessions/{session_id}/messages`` (private, session-scoped)
            - ``sessions/{room}/public`` (open; ``{room}`` is non-magic)

        The subscribe pattern ``sessions/+/messages`` is a wildcard superset
        of the private pattern. It must be rejected because the wildcard would
        cover other sessions' private messages.
        """
        mcp = FastMCP("test")
        mcp.declare_event("sessions/{session_id}/messages")
        mcp.declare_event("sessions/{room}/public")

        async with Client(mcp) as _client:
            session = _get_active_session(mcp)
            result = await _subscribe_via_handler(
                mcp, session, ["sessions/+/messages"]
            )

        assert result.subscribed == []
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "permission_denied"


# ---------------------------------------------------------------------------
# Context._tool_name and auto-source tests
# ---------------------------------------------------------------------------


class TestContextToolName:
    def test_tool_name_set(self):
        """Context created with _tool_name exposes it via tool_name property."""
        mcp = FastMCP("test")
        ctx = Context(mcp, _tool_name="my_tool")
        assert ctx.tool_name == "my_tool"

    def test_tool_name_default_none(self):
        """Context created without _tool_name has tool_name == None."""
        mcp = FastMCP("test")
        ctx = Context(mcp)
        assert ctx.tool_name is None


class TestAutoSourceFromToolName:
    async def test_auto_source_set_from_tool_name(self):
        """When a tool emits an event without explicit source, source is auto-set
        to 'tool/<tool_name>'."""
        mcp_server = FastMCP("test")
        mcp_server.declare_event("myapp/notifications")

        captured_sources: list[str | None] = []
        original_emit = mcp_server.emit_event

        async def tracking_emit(
            topic: str,
            payload: Any,
            *,
            event_id: str | None = None,
            retained: bool | None = None,
            source: str | None = None,
            correlation_id: str | None = None,
            requested_effects: list[EventEffect] | None = None,
            expires_at: str | None = None,
            target_session_ids: Any = None,
        ) -> None:
            captured_sources.append(source)
            await original_emit(
                topic,
                payload,
                event_id=event_id,
                retained=retained,
                source=source,
                correlation_id=correlation_id,
                requested_effects=requested_effects,
                expires_at=expires_at,
                target_session_ids=target_session_ids,
            )

        setattr(mcp_server, "emit_event", tracking_emit)

        @mcp_server.tool
        async def notify(message: str, ctx: Context) -> str:
            await ctx.emit_event("myapp/notifications", {"text": message})
            return "sent"

        async with Client(mcp_server) as client:
            result = await client.call_tool("notify", {"message": "hello"})
            assert result.data == "sent"
            assert len(captured_sources) == 1
            assert captured_sources[0] == "tool/notify"

    async def test_explicit_source_overrides_auto_source(self):
        """When a tool provides explicit source, it is not overridden."""
        mcp_server = FastMCP("test")
        mcp_server.declare_event("myapp/notifications")

        captured_sources: list[str | None] = []
        original_emit = mcp_server.emit_event

        async def tracking_emit(
            topic: str,
            payload: Any,
            *,
            event_id: str | None = None,
            retained: bool | None = None,
            source: str | None = None,
            correlation_id: str | None = None,
            requested_effects: list[EventEffect] | None = None,
            expires_at: str | None = None,
            target_session_ids: Any = None,
        ) -> None:
            captured_sources.append(source)
            await original_emit(
                topic,
                payload,
                event_id=event_id,
                retained=retained,
                source=source,
                correlation_id=correlation_id,
                requested_effects=requested_effects,
                expires_at=expires_at,
                target_session_ids=target_session_ids,
            )

        setattr(mcp_server, "emit_event", tracking_emit)

        @mcp_server.tool
        async def notify_custom(message: str, ctx: Context) -> str:
            await ctx.emit_event(
                "myapp/notifications",
                {"text": message},
                source="custom/source",
            )
            return "sent"

        async with Client(mcp_server) as client:
            result = await client.call_tool("notify_custom", {"message": "hello"})
            assert result.data == "sent"
            assert len(captured_sources) == 1
            assert captured_sources[0] == "custom/source"

    async def test_source_none_when_not_in_tool_context(self):
        """When emit_event is called directly on the server (not via a tool),
        source remains None in the delivered notification."""
        mcp_server = FastMCP("test")
        mcp_server.declare_event("myapp/status")

        received_notifications: list[Any] = []

        async with Client(mcp_server) as _client:
            session = list(mcp_server._active_sessions.values())[0]
            session_id = getattr(session, "_fastmcp_event_session_id")
            await mcp_server._subscription_registry.add(session_id, "myapp/status")

            async def capturing_send(
                notification: ServerNotification,
                related_request_id: str | int | None = None,
            ) -> None:
                received_notifications.append(notification)

            setattr(session, "send_notification", capturing_send)

            # Emit directly on server, not through a tool
            await mcp_server.emit_event("myapp/status", {"state": "running"})

            assert len(received_notifications) == 1
            notif = received_notifications[0]
            # Server-level emit has no tool context, so source should be None
            assert notif.params.source is None
