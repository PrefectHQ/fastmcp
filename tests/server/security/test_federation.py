"""Tests for Federation & Revocation (Phase 16).

Covers CRL management, federation peers, trust score sharing,
revocation propagation, federated queries, and event bus integration.
"""

from __future__ import annotations

import pytest

from fastmcp.server.security.alerts.bus import SecurityEventBus
from fastmcp.server.security.alerts.handlers import BufferedHandler
from fastmcp.server.security.federation.crl import (
    CertificateRevocationList,
    CRLEntry,
    RevocationReason,
)
from fastmcp.server.security.federation.federation import (
    FederatedQuery,
    FederationPeer,
    PeerStatus,
    TrustFederation,
)
from fastmcp.server.security.registry.models import TrustScore
from fastmcp.server.security.registry.registry import TrustRegistry

# ── CRL Entry tests ────────────────────────────────────────────────


class TestCRLEntry:
    def test_default_entry(self):
        entry = CRLEntry()
        assert entry.entry_id
        assert entry.reason == RevocationReason.MANUAL_REVOCATION
        assert not entry.emergency
        assert not entry.propagated

    def test_to_dict(self):
        entry = CRLEntry(
            tool_name="bad-tool",
            reason=RevocationReason.SECURITY_INCIDENT,
            emergency=True,
        )
        d = entry.to_dict()
        assert d["tool_name"] == "bad-tool"
        assert d["reason"] == "security_incident"
        assert d["emergency"] is True


# ── CRL tests ──────────────────────────────────────────────────────


class TestCertificateRevocationList:
    def test_revoke(self):
        crl = CertificateRevocationList()
        entry = crl.revoke("bad-tool", reason=RevocationReason.SECURITY_INCIDENT)
        assert entry.tool_name == "bad-tool"
        assert crl.is_revoked("bad-tool")
        assert crl.entry_count == 1

    def test_revoke_with_attestation_id(self):
        crl = CertificateRevocationList()
        crl.revoke("tool", attestation_id="att-123")
        assert crl.is_attestation_revoked("att-123")

    def test_not_revoked(self):
        crl = CertificateRevocationList()
        assert not crl.is_revoked("good-tool")
        assert not crl.is_attestation_revoked("att-456")

    def test_get_entries(self):
        crl = CertificateRevocationList()
        crl.revoke("tool-a", reason=RevocationReason.KEY_COMPROMISE)
        crl.revoke("tool-a", reason=RevocationReason.POLICY_VIOLATION)
        crl.revoke("tool-b")
        entries = crl.get_entries("tool-a")
        assert len(entries) == 2

    def test_get_entry_by_attestation(self):
        crl = CertificateRevocationList()
        crl.revoke("tool", attestation_id="att-1")
        entry = crl.get_entry_by_attestation("att-1")
        assert entry is not None
        assert entry.tool_name == "tool"

    def test_unrevoke(self):
        crl = CertificateRevocationList()
        crl.revoke("tool-a")
        crl.revoke("tool-a", attestation_id="att-1")
        count = crl.unrevoke("tool-a")
        assert count == 2
        assert not crl.is_revoked("tool-a")
        assert not crl.is_attestation_revoked("att-1")

    def test_unrevoke_nonexistent(self):
        crl = CertificateRevocationList()
        assert crl.unrevoke("nonexistent") == 0

    def test_get_all_entries(self):
        crl = CertificateRevocationList()
        crl.revoke("a")
        crl.revoke("b")
        assert len(crl.get_all_entries()) == 2

    def test_emergency_entries(self):
        crl = CertificateRevocationList()
        crl.revoke("a", emergency=True)
        crl.revoke("b", emergency=False)
        assert len(crl.get_emergency_entries()) == 1

    def test_propagated_entries(self):
        crl = CertificateRevocationList()
        crl.revoke("a", propagated=True, source_peer_id="peer-1")
        crl.revoke("b")
        assert len(crl.get_propagated_entries()) == 1

    def test_to_dict(self):
        crl = CertificateRevocationList(crl_id="test-crl")
        crl.revoke("tool")
        d = crl.to_dict()
        assert d["crl_id"] == "test-crl"
        assert d["entry_count"] == 1

    def test_event_bus_integration(self):
        bus = SecurityEventBus()
        handler = BufferedHandler(max_size=50)
        bus.subscribe(handler)
        crl = CertificateRevocationList(event_bus=bus)
        crl.revoke("bad-tool", emergency=True)
        assert len(handler.events) == 1
        assert handler.events[0].layer == "federation"


# ── Federation Peer tests ──────────────────────────────────────────


class TestFederationPeer:
    def test_default_peer(self):
        peer = FederationPeer()
        assert peer.peer_id
        assert peer.status == PeerStatus.ACTIVE
        assert peer.trust_weight == 0.5

    def test_to_dict(self):
        peer = FederationPeer(name="partner", endpoint="https://partner.example.com")
        d = peer.to_dict()
        assert d["name"] == "partner"
        assert d["status"] == "active"


# ── Trust Federation tests ─────────────────────────────────────────


class TestTrustFederation:
    def test_add_peer(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner", endpoint="https://partner.example.com")
        assert peer.name == "partner"
        assert fed.peer_count == 1

    def test_remove_peer(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        assert fed.remove_peer(peer.peer_id)
        assert fed.peer_count == 0

    def test_remove_nonexistent(self):
        fed = TrustFederation()
        assert not fed.remove_peer("nonexistent")

    def test_get_peer(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        assert fed.get_peer(peer.peer_id) is peer

    def test_get_all_peers(self):
        fed = TrustFederation()
        fed.add_peer("a")
        fed.add_peer("b")
        assert len(fed.get_all_peers()) == 2

    def test_update_peer_status(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.update_peer_status(peer.peer_id, PeerStatus.SUSPENDED)
        assert peer.status == PeerStatus.SUSPENDED

    def test_active_peer_count(self):
        fed = TrustFederation()
        p1 = fed.add_peer("a")
        fed.add_peer("b")
        fed.update_peer_status(p1.peer_id, PeerStatus.SUSPENDED)
        assert fed.active_peer_count == 1


# ── Trust score sharing tests ──────────────────────────────────────


class TestTrustScoreSharing:
    def test_receive_trust_score(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        score = TrustScore(overall=0.85)
        assert fed.receive_trust_score(peer.peer_id, "tool-a", score)

    def test_receive_from_nonexistent_peer(self):
        fed = TrustFederation()
        score = TrustScore(overall=0.85)
        assert not fed.receive_trust_score("nonexistent", "tool-a", score)

    def test_ignore_suspended_peer_score(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.update_peer_status(peer.peer_id, PeerStatus.SUSPENDED)
        score = TrustScore(overall=0.85)
        assert not fed.receive_trust_score(peer.peer_id, "tool-a", score)

    def test_score_updates_peer_stats(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.receive_trust_score(peer.peer_id, "tool-a", TrustScore(overall=0.8))
        assert peer.shared_scores == 1
        assert peer.last_sync is not None


# ── Revocation propagation tests ───────────────────────────────────


class TestRevocationPropagation:
    def test_receive_revocation(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        entry = fed.receive_revocation(peer.peer_id, "bad-tool")
        assert entry is not None
        assert entry.propagated
        assert entry.source_peer_id == peer.peer_id
        assert fed.local_crl.is_revoked("bad-tool")

    def test_receive_emergency_revocation(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        entry = fed.receive_revocation(peer.peer_id, "dangerous-tool", emergency=True)
        assert entry is not None
        assert entry.emergency

    def test_ignore_suspended_peer_revocation(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.update_peer_status(peer.peer_id, PeerStatus.SUSPENDED)
        entry = fed.receive_revocation(peer.peer_id, "tool")
        assert entry is None

    def test_revocation_updates_peer_stats(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.receive_revocation(peer.peer_id, "tool")
        assert peer.shared_revocations == 1

    def test_broadcast_revocation(self):
        fed = TrustFederation()
        fed.add_peer("a")
        fed.add_peer("b")
        entries = fed.broadcast_revocation("bad-tool", emergency=True)
        assert len(entries) == 1
        assert fed.local_crl.is_revoked("bad-tool")


# ── Federated query tests ──────────────────────────────────────────


class TestFederatedQuery:
    def test_query_local_only(self):
        registry = TrustRegistry()
        registry.register("my-tool", attestation=None)
        fed = TrustFederation(local_registry=registry)
        result = fed.query("my-tool")
        assert result.tool_name == "my-tool"
        assert result.local_score is not None

    def test_query_with_peer_scores(self):
        registry = TrustRegistry()
        registry.register("tool-a", attestation=None)
        fed = TrustFederation(local_registry=registry)
        peer = fed.add_peer("partner", trust_weight=0.8)
        fed.receive_trust_score(peer.peer_id, "tool-a", TrustScore(overall=0.9))
        result = fed.query("tool-a")
        assert result.contributing_peers == 1
        assert len(result.peer_scores) == 1
        assert result.merged_score > 0

    def test_query_merged_score_weighting(self):
        fed = TrustFederation(local_registry=TrustRegistry())
        # No local score for this tool, only peer
        peer = fed.add_peer("partner", trust_weight=0.5)
        fed.receive_trust_score(peer.peer_id, "tool-x", TrustScore(overall=0.8))
        result = fed.query("tool-x")
        assert result.merged_score == pytest.approx(0.8)

    def test_query_revoked_tool(self):
        fed = TrustFederation()
        fed.local_crl.revoke("bad-tool")
        result = fed.query("bad-tool")
        assert result.is_revoked
        assert len(result.revocation_entries) == 1

    def test_query_excludes_suspended_peers(self):
        fed = TrustFederation()
        peer = fed.add_peer("partner")
        fed.receive_trust_score(peer.peer_id, "tool", TrustScore(overall=0.9))
        fed.update_peer_status(peer.peer_id, PeerStatus.SUSPENDED)
        result = fed.query("tool")
        assert result.contributing_peers == 0

    def test_query_min_peer_weight(self):
        fed = TrustFederation()
        p1 = fed.add_peer("weak", trust_weight=0.1)
        p2 = fed.add_peer("strong", trust_weight=0.9)
        fed.receive_trust_score(p1.peer_id, "tool", TrustScore(overall=0.5))
        fed.receive_trust_score(p2.peer_id, "tool", TrustScore(overall=0.9))
        q = FederatedQuery(tool_name="tool", min_peer_weight=0.5)
        result = fed.query("tool", query=q)
        assert result.contributing_peers == 1  # Only the strong peer

    def test_query_result_serialization(self):
        fed = TrustFederation()
        fed.local_crl.revoke("tool")
        result = fed.query("tool")
        d = result.to_dict()
        assert d["is_revoked"] is True
        assert "tool_name" in d

    def test_query_not_found(self):
        fed = TrustFederation()
        result = fed.query("nonexistent")
        assert result.local_score is None
        assert result.merged_score == 0.0
        assert not result.is_revoked


# ── Federation status tests ────────────────────────────────────────


class TestFederationStatus:
    def test_status(self):
        fed = TrustFederation(federation_id="test-fed")
        fed.add_peer("a")
        fed.add_peer("b")
        fed.local_crl.revoke("bad-tool")
        status = fed.get_federation_status()
        assert status["federation_id"] == "test-fed"
        assert status["peer_count"] == 2
        assert status["crl_entries"] == 1


# ── Import tests ───────────────────────────────────────────────────


class TestImports:
    def test_federation_imports(self):
        from fastmcp.server.security.federation import (
            TrustFederation,
        )

        assert TrustFederation is not None

    def test_top_level_imports(self):
        from fastmcp.server.security import (
            TrustFederation,
        )

        assert TrustFederation is not None
