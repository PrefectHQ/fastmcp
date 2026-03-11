"""Tests for the MemoryBackend storage implementation."""

from fastmcp.server.security.storage.memory import MemoryBackend


class TestMemoryBackendProvenance:
    def test_append_and_load(self):
        backend = MemoryBackend()
        backend.append_provenance_record("ledger-1", {"record_id": "r1", "data": "x"})
        backend.append_provenance_record("ledger-1", {"record_id": "r2", "data": "y"})
        records = backend.load_provenance_records("ledger-1")
        assert len(records) == 2
        assert records[0]["record_id"] == "r1"
        assert records[1]["record_id"] == "r2"

    def test_load_empty(self):
        backend = MemoryBackend()
        assert backend.load_provenance_records("nonexistent") == []

    def test_namespace_isolation(self):
        backend = MemoryBackend()
        backend.append_provenance_record("a", {"id": "1"})
        backend.append_provenance_record("b", {"id": "2"})
        assert len(backend.load_provenance_records("a")) == 1
        assert len(backend.load_provenance_records("b")) == 1


class TestMemoryBackendExchange:
    def test_append_and_load(self):
        backend = MemoryBackend()
        backend.append_exchange_entry("log-1", {"entry_id": "e1"})
        backend.append_exchange_entry("log-1", {"entry_id": "e2"})
        entries = backend.load_exchange_entries("log-1")
        assert len(entries) == 2

    def test_load_empty(self):
        backend = MemoryBackend()
        assert backend.load_exchange_entries("x") == []


class TestMemoryBackendContracts:
    def test_save_and_load(self):
        backend = MemoryBackend()
        backend.save_contract("broker-1", "c1", {"status": "active"})
        backend.save_contract("broker-1", "c2", {"status": "pending"})
        contracts = backend.load_contracts("broker-1")
        assert len(contracts) == 2
        assert contracts["c1"]["status"] == "active"

    def test_overwrite(self):
        backend = MemoryBackend()
        backend.save_contract("b", "c1", {"status": "active"})
        backend.save_contract("b", "c1", {"status": "revoked"})
        contracts = backend.load_contracts("b")
        assert contracts["c1"]["status"] == "revoked"

    def test_remove(self):
        backend = MemoryBackend()
        backend.save_contract("b", "c1", {"status": "active"})
        backend.remove_contract("b", "c1")
        assert backend.load_contracts("b") == {}

    def test_remove_nonexistent(self):
        backend = MemoryBackend()
        backend.remove_contract("b", "nonexistent")  # Should not raise


class TestMemoryBackendBaselines:
    def test_save_and_load(self):
        backend = MemoryBackend()
        backend.save_baseline("analyzer-1", "agent-1", "metric-a", {"mean": 5.0})
        backend.save_baseline("analyzer-1", "agent-1", "metric-b", {"mean": 10.0})
        baselines = backend.load_baselines("analyzer-1")
        assert "agent-1" in baselines
        assert "metric-a" in baselines["agent-1"]
        assert baselines["agent-1"]["metric-a"]["mean"] == 5.0
        assert baselines["agent-1"]["metric-b"]["mean"] == 10.0

    def test_remove_single(self):
        backend = MemoryBackend()
        backend.save_baseline("a", "actor", "m1", {"v": 1})
        backend.save_baseline("a", "actor", "m2", {"v": 2})
        backend.remove_baseline("a", "actor", "m1")
        baselines = backend.load_baselines("a")
        assert "m1" not in baselines.get("actor", {})
        assert "m2" in baselines["actor"]

    def test_remove_all_for_actor(self):
        backend = MemoryBackend()
        backend.save_baseline("a", "actor", "m1", {"v": 1})
        backend.save_baseline("a", "actor", "m2", {"v": 2})
        backend.remove_baseline("a", "actor")
        baselines = backend.load_baselines("a")
        assert "actor" not in baselines

    def test_load_empty(self):
        backend = MemoryBackend()
        assert backend.load_baselines("x") == {}


class TestMemoryBackendDrift:
    def test_append_and_load(self):
        backend = MemoryBackend()
        backend.append_drift_event("a", {"event_id": "d1"})
        backend.append_drift_event("a", {"event_id": "d2"})
        events = backend.load_drift_history("a")
        assert len(events) == 2

    def test_load_empty(self):
        backend = MemoryBackend()
        assert backend.load_drift_history("x") == []


class TestMemoryBackendEscalations:
    def test_append_and_load(self):
        backend = MemoryBackend()
        backend.append_escalation("eng-1", {"action": "alert"})
        escalations = backend.load_escalations("eng-1")
        assert len(escalations) == 1

    def test_load_empty(self):
        backend = MemoryBackend()
        assert backend.load_escalations("x") == []


class TestMemoryBackendConsent:
    def test_nodes(self):
        backend = MemoryBackend()
        backend.save_consent_node("g", "n1", {"type": "user"})
        backend.save_consent_node("g", "n2", {"type": "agent"})
        graph = backend.load_consent_graph("g")
        assert len(graph["nodes"]) == 2

    def test_remove_node(self):
        backend = MemoryBackend()
        backend.save_consent_node("g", "n1", {"type": "user"})
        backend.remove_consent_node("g", "n1")
        graph = backend.load_consent_graph("g")
        assert "n1" not in graph["nodes"]

    def test_edges(self):
        backend = MemoryBackend()
        backend.save_consent_edge("g", "e1", {"source": "a", "target": "b"})
        graph = backend.load_consent_graph("g")
        assert "e1" in graph["edges"]

    def test_remove_edge(self):
        backend = MemoryBackend()
        backend.save_consent_edge("g", "e1", {"source": "a"})
        backend.remove_consent_edge("g", "e1")
        graph = backend.load_consent_graph("g")
        assert "e1" not in graph["edges"]

    def test_groups(self):
        backend = MemoryBackend()
        backend.save_consent_group("g", "group1", ["a", "b", "c"])
        graph = backend.load_consent_graph("g")
        assert graph["groups"]["group1"] == ["a", "b", "c"]

    def test_remove_group(self):
        backend = MemoryBackend()
        backend.save_consent_group("g", "group1", ["a"])
        backend.remove_consent_group("g", "group1")
        graph = backend.load_consent_graph("g")
        assert "group1" not in graph["groups"]

    def test_audit_log(self):
        backend = MemoryBackend()
        backend.append_consent_audit("g", {"action": "grant"})
        backend.append_consent_audit("g", {"action": "revoke"})
        graph = backend.load_consent_graph("g")
        assert len(graph["audit_log"]) == 2

    def test_empty_graph(self):
        backend = MemoryBackend()
        graph = backend.load_consent_graph("nonexistent")
        assert graph["nodes"] == {}
        assert graph["edges"] == {}
        assert graph["groups"] == {}
        assert graph["audit_log"] == []


class TestMemoryBackendMarketplace:
    def test_server_registration(self):
        backend = MemoryBackend()
        backend.save_server_registration("mp", "srv1", {"name": "Test"})
        mp = backend.load_marketplace("mp")
        assert "srv1" in mp["servers"]

    def test_remove_registration(self):
        backend = MemoryBackend()
        backend.save_server_registration("mp", "srv1", {"name": "Test"})
        backend.remove_server_registration("mp", "srv1")
        mp = backend.load_marketplace("mp")
        assert "srv1" not in mp["servers"]

    def test_audit_log(self):
        backend = MemoryBackend()
        backend.append_marketplace_audit("mp", {"action": "register"})
        mp = backend.load_marketplace("mp")
        assert len(mp["audit_log"]) == 1

    def test_empty_marketplace(self):
        backend = MemoryBackend()
        mp = backend.load_marketplace("nonexistent")
        assert mp["servers"] == {}
        assert mp["audit_log"] == []


class TestMemoryBackendToolMarketplace:
    def test_listing_install_and_review_roundtrip(self):
        backend = MemoryBackend()
        backend.save_tool_listing("tools", "listing-1", {"tool_name": "weather"})
        backend.append_tool_install("tools", "listing-1", {"install_id": "i1"})
        backend.append_tool_review("tools", "listing-1", {"review_id": "r1"})

        data = backend.load_tool_marketplace("tools")

        assert data["listings"]["listing-1"]["tool_name"] == "weather"
        assert data["installs"]["listing-1"][0]["install_id"] == "i1"
        assert data["reviews"]["listing-1"][0]["review_id"] == "r1"

    def test_remove_listing_clears_related_state(self):
        backend = MemoryBackend()
        backend.save_tool_listing("tools", "listing-1", {"tool_name": "weather"})
        backend.append_tool_install("tools", "listing-1", {"install_id": "i1"})
        backend.append_tool_review("tools", "listing-1", {"review_id": "r1"})

        backend.remove_tool_listing("tools", "listing-1")
        data = backend.load_tool_marketplace("tools")

        assert "listing-1" not in data["listings"]
        assert "listing-1" not in data["installs"]
        assert "listing-1" not in data["reviews"]
