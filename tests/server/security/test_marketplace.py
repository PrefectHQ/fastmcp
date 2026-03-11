"""Tests for the Marketplace registry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.gateway.models import (
    ServerCapability,
    TrustLevel,
)


class TestMarketplaceRegistration:
    def test_register_server(self):
        mp = Marketplace()
        reg = mp.register(
            name="Test Server",
            endpoint="http://localhost:8000",
        )
        assert reg.name == "Test Server"
        assert mp.server_count == 1

    def test_register_with_capabilities(self):
        mp = Marketplace()
        reg = mp.register(
            name="Secure Server",
            endpoint="http://localhost",
            capabilities={
                ServerCapability.POLICY_ENGINE,
                ServerCapability.PROVENANCE_LEDGER,
            },
        )
        assert ServerCapability.POLICY_ENGINE in reg.capabilities

    def test_register_with_explicit_id(self):
        mp = Marketplace()
        reg = mp.register(
            name="Test",
            endpoint="http://test",
            server_id="my-server-id",
        )
        assert reg.server_id == "my-server-id"

    def test_unregister(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        assert mp.unregister(reg.server_id)
        assert mp.server_count == 0

    def test_unregister_not_found(self):
        mp = Marketplace()
        assert not mp.unregister("nonexistent")

    def test_get_server(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        found = mp.get(reg.server_id)
        assert found is not None
        assert found.name == "Test"

    def test_get_server_not_found(self):
        mp = Marketplace()
        assert mp.get("nonexistent") is None


class TestMarketplaceHeartbeat:
    def test_heartbeat_updates(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        old_heartbeat = reg.last_heartbeat
        assert mp.heartbeat(reg.server_id)
        assert reg.last_heartbeat >= old_heartbeat

    def test_heartbeat_not_found(self):
        mp = Marketplace()
        assert not mp.heartbeat("nonexistent")


class TestMarketplaceTrustLevel:
    def test_update_trust_level(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        assert mp.update_trust_level(reg.server_id, TrustLevel.COMMUNITY_VERIFIED)
        assert reg.trust_level == TrustLevel.COMMUNITY_VERIFIED

    def test_update_trust_not_found(self):
        mp = Marketplace()
        assert not mp.update_trust_level("nope", TrustLevel.AUDITOR_VERIFIED)

    def test_trust_update_audit_logged(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        mp.update_trust_level(reg.server_id, TrustLevel.SELF_CERTIFIED)
        log = mp.get_audit_log()
        trust_entries = [e for e in log if e["action"] == "trust_update"]
        assert len(trust_entries) == 1


class TestMarketplaceSearch:
    def _setup_marketplace(self) -> Marketplace:
        mp = Marketplace()
        mp.register(
            name="Server A",
            endpoint="http://a",
            capabilities={ServerCapability.POLICY_ENGINE},
            trust_level=TrustLevel.UNVERIFIED,
            tags={"security"},
        )
        mp.register(
            name="Server B",
            endpoint="http://b",
            capabilities={
                ServerCapability.POLICY_ENGINE,
                ServerCapability.PROVENANCE_LEDGER,
            },
            trust_level=TrustLevel.COMMUNITY_VERIFIED,
            tags={"security", "audit"},
        )
        mp.register(
            name="Data Server",
            endpoint="http://c",
            capabilities={ServerCapability.CONSENT_GRAPH},
            trust_level=TrustLevel.AUDITOR_VERIFIED,
            tags={"data"},
        )
        return mp

    def test_search_all(self):
        mp = self._setup_marketplace()
        results = mp.search()
        assert len(results) == 3

    def test_search_by_capability(self):
        mp = self._setup_marketplace()
        results = mp.search(capabilities={ServerCapability.POLICY_ENGINE})
        assert len(results) == 2

    def test_search_by_multiple_capabilities(self):
        mp = self._setup_marketplace()
        results = mp.search(
            capabilities={
                ServerCapability.POLICY_ENGINE,
                ServerCapability.PROVENANCE_LEDGER,
            }
        )
        assert len(results) == 1
        assert results[0].name == "Server B"

    def test_search_by_trust_level(self):
        mp = self._setup_marketplace()
        results = mp.search(trust_level=TrustLevel.AUDITOR_VERIFIED)
        assert len(results) == 1
        assert results[0].name == "Data Server"

    def test_search_by_min_trust_level(self):
        mp = self._setup_marketplace()
        results = mp.search(min_trust_level=TrustLevel.COMMUNITY_VERIFIED)
        assert len(results) == 2  # COMMUNITY_VERIFIED and AUDITOR_VERIFIED

    def test_search_by_tags(self):
        mp = self._setup_marketplace()
        results = mp.search(tags={"audit"})
        assert len(results) == 1

    def test_search_by_name(self):
        mp = self._setup_marketplace()
        results = mp.search(name_contains="data")
        assert len(results) == 1
        assert results[0].name == "Data Server"

    def test_search_healthy_only(self):
        mp = Marketplace()
        mp.register(name="Healthy", endpoint="http://h")
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        reg = mp.register(name="Stale", endpoint="http://s")
        reg.last_heartbeat = old

        results = mp.search(healthy_only=True)
        assert len(results) == 1
        assert results[0].name == "Healthy"

    def test_search_with_limit(self):
        mp = self._setup_marketplace()
        results = mp.search(limit=2)
        assert len(results) == 2


class TestMarketplaceAudit:
    def test_audit_log_register(self):
        mp = Marketplace()
        mp.register(name="Test", endpoint="http://test")
        log = mp.get_audit_log()
        assert len(log) == 1
        assert log[0]["action"] == "register"

    def test_audit_log_unregister(self):
        mp = Marketplace()
        reg = mp.register(name="Test", endpoint="http://test")
        mp.unregister(reg.server_id)
        log = mp.get_audit_log()
        assert len(log) == 2
        assert log[0]["action"] == "unregister"

    def test_get_all_servers(self):
        mp = Marketplace()
        mp.register(name="A", endpoint="http://a")
        mp.register(name="B", endpoint="http://b")
        all_servers = mp.get_all_servers()
        assert len(all_servers) == 2
