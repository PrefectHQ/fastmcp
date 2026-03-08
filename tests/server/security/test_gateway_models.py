"""Tests for API Gateway data models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp.server.security.gateway.models import (
    AuditQuery,
    AuditQueryType,
    AuditResult,
    HealthStatus,
    SecurityStatus,
    ServerCapability,
    ServerRegistration,
    TrustLevel,
)


class TestAuditModels:
    def test_audit_query_defaults(self):
        q = AuditQuery()
        assert q.query_type == AuditQueryType.PROVENANCE
        assert q.limit == 100
        assert q.offset == 0

    def test_audit_result_defaults(self):
        r = AuditResult()
        assert r.total_count == 0
        assert r.records == []
        assert not r.has_more


class TestServerRegistration:
    def test_default_registration(self):
        reg = ServerRegistration(name="test", endpoint="http://localhost")
        assert reg.name == "test"
        assert reg.trust_level == TrustLevel.UNVERIFIED
        assert len(reg.server_id) > 0

    def test_is_healthy_recent(self):
        reg = ServerRegistration()
        assert reg.is_healthy()

    def test_is_healthy_stale(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        reg = ServerRegistration(last_heartbeat=old)
        assert not reg.is_healthy(timeout_seconds=300)

    def test_has_capability(self):
        reg = ServerRegistration(
            capabilities={ServerCapability.POLICY_ENGINE, ServerCapability.AUDIT_API}
        )
        assert reg.has_capability(ServerCapability.POLICY_ENGINE)
        assert not reg.has_capability(ServerCapability.CONSENT_GRAPH)

    def test_to_dict(self):
        reg = ServerRegistration(
            name="test",
            endpoint="http://test",
            capabilities={ServerCapability.POLICY_ENGINE},
            tags={"security", "audit"},
        )
        d = reg.to_dict()
        assert d["name"] == "test"
        assert "policy_engine" in d["capabilities"]
        assert set(d["tags"]) == {"security", "audit"}

    def test_unique_server_ids(self):
        r1 = ServerRegistration()
        r2 = ServerRegistration()
        assert r1.server_id != r2.server_id


class TestSecurityStatus:
    def test_default_status(self):
        s = SecurityStatus()
        assert s.status == HealthStatus.HEALTHY

    def test_to_dict(self):
        s = SecurityStatus(
            status=HealthStatus.DEGRADED,
            layers={"provenance": {"enabled": True}},
            total_operations=42,
        )
        d = s.to_dict()
        assert d["status"] == "degraded"
        assert d["total_operations"] == 42
        assert "provenance" in d["layers"]


class TestEnums:
    def test_audit_query_types(self):
        assert AuditQueryType.PROVENANCE.value == "provenance"
        assert AuditQueryType.DRIFT.value == "drift"
        assert AuditQueryType.CONSENT.value == "consent"

    def test_server_capabilities(self):
        assert len(ServerCapability) == 6

    def test_trust_levels(self):
        levels = list(TrustLevel)
        assert levels[0] == TrustLevel.UNVERIFIED
        assert levels[-1] == TrustLevel.AUDITOR_VERIFIED
