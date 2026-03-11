"""Tests for policy decision audit log."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from fastmcp.server.security.policy.audit import AuditEntry, PolicyAuditLog
from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)


def _ctx(
    resource_id: str = "tool-a",
    action: str = "call_tool",
    actor_id: str = "agent-1",
    metadata: dict | None = None,
    tags: frozenset[str] | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id=actor_id,
        action=action,
        resource_id=resource_id,
        metadata=metadata or {},
        tags=tags or frozenset(),
    )


def _result(
    decision: PolicyDecision = PolicyDecision.ALLOW,
    reason: str = "test reason",
    policy_id: str = "test-policy",
    constraints: list[str] | None = None,
) -> PolicyResult:
    return PolicyResult(
        decision=decision,
        reason=reason,
        policy_id=policy_id,
        constraints=constraints or [],
    )


# ── AuditEntry tests ──────────────────────────────────────────────


class TestAuditEntry:
    def test_to_dict(self):
        entry = AuditEntry(
            actor_id="agent-1",
            action="call_tool",
            resource_id="tool-a",
            decision=PolicyDecision.ALLOW,
            reason="allowed",
            policy_id="test-policy",
            constraints=["read-only"],
            metadata={"key": "val"},
            tags=frozenset({"internal"}),
        )
        d = entry.to_dict()
        assert d["actor_id"] == "agent-1"
        assert d["decision"] == "allow"
        assert d["constraints"] == ["read-only"]
        assert d["metadata"] == {"key": "val"}
        assert d["tags"] == ["internal"]
        assert "timestamp" in d

    def test_to_dict_json_serializable(self):
        entry = AuditEntry(
            actor_id="x",
            action="y",
            resource_id="z",
            decision=PolicyDecision.DENY,
            reason="nope",
            policy_id="p",
        )
        serialized = json.dumps(entry.to_dict())
        assert len(serialized) > 0


# ── Basic recording ────────────────────────────────────────────────


class TestBasicRecording:
    def test_record_and_size(self):
        log = PolicyAuditLog()
        log.record(_ctx(), _result())
        assert log.size == 1

    def test_record_returns_entry(self):
        log = PolicyAuditLog()
        entry = log.record(_ctx(resource_id="tool-x"), _result())
        assert isinstance(entry, AuditEntry)
        assert entry.resource_id == "tool-x"

    def test_record_captures_context(self):
        log = PolicyAuditLog()
        ctx = _ctx(
            resource_id="my-tool",
            action="read_resource",
            actor_id="admin",
            metadata={"env": "prod"},
            tags=frozenset({"safe"}),
        )
        entry = log.record(ctx, _result())
        assert entry.actor_id == "admin"
        assert entry.action == "read_resource"
        assert entry.resource_id == "my-tool"
        assert entry.metadata == {"env": "prod"}
        assert entry.tags == frozenset({"safe"})

    def test_record_captures_result(self):
        log = PolicyAuditLog()
        result = _result(
            decision=PolicyDecision.DENY,
            reason="blocked by policy",
            policy_id="my-policy",
            constraints=["no-write"],
        )
        entry = log.record(_ctx(), result)
        assert entry.decision == PolicyDecision.DENY
        assert entry.reason == "blocked by policy"
        assert entry.policy_id == "my-policy"
        assert entry.constraints == ["no-write"]

    def test_record_elapsed_ms(self):
        log = PolicyAuditLog()
        entry = log.record(_ctx(), _result(), elapsed_ms=42.5)
        assert entry.elapsed_ms == 42.5

    def test_total_counters(self):
        log = PolicyAuditLog()
        log.record(_ctx(), _result(decision=PolicyDecision.ALLOW))
        log.record(_ctx(), _result(decision=PolicyDecision.ALLOW))
        log.record(_ctx(), _result(decision=PolicyDecision.DENY))
        assert log.total_recorded == 3
        assert log.total_allowed == 2
        assert log.total_denied == 1


# ── Bounded capacity ──────────────────────────────────────────────


class TestBoundedCapacity:
    def test_max_entries_enforced(self):
        log = PolicyAuditLog(max_entries=3)
        for i in range(5):
            log.record(_ctx(resource_id=f"tool-{i}"), _result())
        assert log.size == 3
        assert log.total_recorded == 5

    def test_oldest_entries_evicted(self):
        log = PolicyAuditLog(max_entries=2)
        log.record(_ctx(resource_id="first"), _result())
        log.record(_ctx(resource_id="second"), _result())
        log.record(_ctx(resource_id="third"), _result())

        entries = log.export()
        assert len(entries) == 2
        assert entries[0]["resource_id"] == "second"
        assert entries[1]["resource_id"] == "third"

    def test_max_entries_property(self):
        log = PolicyAuditLog(max_entries=500)
        assert log.max_entries == 500


# ── Query functionality ────────────────────────────────────────────


class TestQuery:
    def _populate(self, log: PolicyAuditLog):
        """Add a mix of entries for query tests."""
        log.record(
            _ctx(resource_id="tool-a", actor_id="alice", action="call_tool"),
            _result(decision=PolicyDecision.ALLOW, policy_id="policy-1"),
        )
        log.record(
            _ctx(resource_id="tool-b", actor_id="bob", action="call_tool"),
            _result(decision=PolicyDecision.DENY, policy_id="policy-1"),
        )
        log.record(
            _ctx(resource_id="admin-panel", actor_id="alice", action="read_resource"),
            _result(decision=PolicyDecision.DENY, policy_id="policy-2"),
        )
        log.record(
            _ctx(resource_id="tool-a", actor_id="charlie", action="call_tool"),
            _result(decision=PolicyDecision.ALLOW, policy_id="policy-1"),
        )

    def test_query_all(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query()
        assert len(entries) == 4

    def test_query_by_actor(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(actor_id="alice")
        assert len(entries) == 2
        assert all(e.actor_id == "alice" for e in entries)

    def test_query_by_resource_exact(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(resource_id="tool-a")
        assert len(entries) == 2

    def test_query_by_resource_glob(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(resource_id="tool-*")
        assert len(entries) == 3  # tool-a, tool-b, tool-a

    def test_query_by_decision(self):
        log = PolicyAuditLog()
        self._populate(log)
        denied = log.query(decision=PolicyDecision.DENY)
        assert len(denied) == 2
        assert all(e.decision == PolicyDecision.DENY for e in denied)

    def test_query_by_action(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(action="read_resource")
        assert len(entries) == 1
        assert entries[0].resource_id == "admin-panel"

    def test_query_by_policy_id(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(policy_id="policy-2")
        assert len(entries) == 1

    def test_query_with_limit(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(limit=2)
        assert len(entries) == 2

    def test_query_returns_most_recent_first(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query()
        # Most recent should be charlie's tool-a access
        assert entries[0].actor_id == "charlie"

    def test_query_combined_filters(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(
            actor_id="alice",
            decision=PolicyDecision.DENY,
        )
        assert len(entries) == 1
        assert entries[0].resource_id == "admin-panel"

    def test_query_since(self):
        log = PolicyAuditLog()
        now = datetime.now(timezone.utc)
        log.record(_ctx(resource_id="old"), _result())
        entries = log.query(since=now - timedelta(seconds=1))
        assert len(entries) == 1

    def test_query_no_results(self):
        log = PolicyAuditLog()
        self._populate(log)
        entries = log.query(actor_id="nonexistent")
        assert len(entries) == 0


# ── Clear ──────────────────────────────────────────────────────────


class TestClear:
    def test_clear_returns_count(self):
        log = PolicyAuditLog()
        log.record(_ctx(), _result())
        log.record(_ctx(), _result())
        removed = log.clear()
        assert removed == 2
        assert log.size == 0

    def test_clear_preserves_counters(self):
        log = PolicyAuditLog()
        log.record(_ctx(), _result(decision=PolicyDecision.ALLOW))
        log.record(_ctx(), _result(decision=PolicyDecision.DENY))
        log.clear()
        # Total counters should persist across clears
        assert log.total_recorded == 2
        assert log.total_allowed == 1
        assert log.total_denied == 1


# ── Export ─────────────────────────────────────────────────────────


class TestExport:
    def test_export_returns_dicts(self):
        log = PolicyAuditLog()
        log.record(_ctx(resource_id="tool-1"), _result())
        log.record(_ctx(resource_id="tool-2"), _result())
        exported = log.export()
        assert len(exported) == 2
        assert all(isinstance(e, dict) for e in exported)

    def test_export_oldest_first(self):
        log = PolicyAuditLog()
        log.record(_ctx(resource_id="first"), _result())
        log.record(_ctx(resource_id="second"), _result())
        exported = log.export()
        assert exported[0]["resource_id"] == "first"
        assert exported[1]["resource_id"] == "second"

    def test_export_json_serializable(self):
        log = PolicyAuditLog()
        log.record(
            _ctx(metadata={"key": "val"}, tags=frozenset({"tag"})),
            _result(constraints=["c1"]),
        )
        serialized = json.dumps(log.export())
        assert len(serialized) > 0


# ── Statistics ─────────────────────────────────────────────────────


class TestStatistics:
    def test_basic_statistics(self):
        log = PolicyAuditLog(max_entries=100)
        log.record(
            _ctx(actor_id="a", resource_id="tool-1"),
            _result(decision=PolicyDecision.ALLOW),
        )
        log.record(
            _ctx(actor_id="b", resource_id="tool-2"),
            _result(decision=PolicyDecision.DENY),
        )
        log.record(
            _ctx(actor_id="a", resource_id="tool-2"),
            _result(decision=PolicyDecision.DENY),
        )

        stats = log.get_statistics()
        assert stats["entries_in_log"] == 3
        assert stats["total_recorded"] == 3
        assert stats["total_allowed"] == 1
        assert stats["total_denied"] == 2
        assert stats["current_allow"] == 1
        assert stats["current_deny"] == 2
        assert stats["unique_actors"] == 2
        assert stats["unique_resources"] == 2

    def test_deny_rate(self):
        log = PolicyAuditLog()
        log.record(_ctx(), _result(decision=PolicyDecision.ALLOW))
        log.record(_ctx(), _result(decision=PolicyDecision.DENY))
        stats = log.get_statistics()
        assert stats["deny_rate"] == 0.5

    def test_deny_rate_empty(self):
        log = PolicyAuditLog()
        stats = log.get_statistics()
        assert stats["deny_rate"] == 0.0

    def test_top_denied_resources(self):
        log = PolicyAuditLog()
        for _ in range(3):
            log.record(_ctx(resource_id="admin"), _result(decision=PolicyDecision.DENY))
        for _ in range(2):
            log.record(_ctx(resource_id="debug"), _result(decision=PolicyDecision.DENY))
        log.record(_ctx(resource_id="tool-a"), _result(decision=PolicyDecision.DENY))

        stats = log.get_statistics()
        top = stats["top_denied_resources"]
        assert top[0]["resource_id"] == "admin"
        assert top[0]["count"] == 3
        assert top[1]["resource_id"] == "debug"
        assert top[1]["count"] == 2


# ── Integration with PolicyEngine ──────────────────────────────────


class TestEngineIntegration:
    @pytest.mark.anyio
    async def test_audit_log_with_engine(self):
        """Show how to wire an audit log to engine manually."""
        from fastmcp.server.security.policy.engine import PolicyEngine
        from fastmcp.server.security.policy.policies.allowlist import (
            AllowlistPolicy,
        )

        audit = PolicyAuditLog()
        engine = PolicyEngine(providers=[AllowlistPolicy(allowed={"safe-*"})])

        # Evaluate and record
        ctx = _ctx(resource_id="safe-tool")
        result = await engine.evaluate(ctx)
        audit.record(ctx, result)

        ctx2 = _ctx(resource_id="blocked-tool")
        result2 = await engine.evaluate(ctx2)
        audit.record(ctx2, result2)

        assert audit.size == 2
        denied = audit.query(decision=PolicyDecision.DENY)
        assert len(denied) == 1
        assert denied[0].resource_id == "blocked-tool"


# ── Import tests ───────────────────────────────────────────────────


class TestImports:
    def test_import_from_audit_module(self):
        from fastmcp.server.security.policy.audit import (
            AuditEntry,
            PolicyAuditLog,
        )

        assert AuditEntry is not None
        assert PolicyAuditLog is not None

    def test_import_from_policy_package(self):
        from fastmcp.server.security.policy import (
            AuditEntry,
            PolicyAuditLog,
        )

        assert AuditEntry is not None
        assert PolicyAuditLog is not None
