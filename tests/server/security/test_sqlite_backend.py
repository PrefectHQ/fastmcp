"""Tests for the SQLiteBackend storage implementation."""

import os
import tempfile

import pytest

from fastmcp.server.security.storage.sqlite import SQLiteBackend


@pytest.fixture()
def db_path(tmp_path):
    """Provide a temp database path."""
    return str(tmp_path / "test.db")


@pytest.fixture()
def backend(db_path):
    """Provide a fresh SQLiteBackend."""
    b = SQLiteBackend(db_path)
    yield b
    b.close()


class TestSQLiteBackendProvenance:
    def test_append_and_load(self, backend):
        backend.append_provenance_record("ledger-1", {"record_id": "r1", "data": "x"})
        backend.append_provenance_record("ledger-1", {"record_id": "r2", "data": "y"})
        records = backend.load_provenance_records("ledger-1")
        assert len(records) == 2
        assert records[0]["record_id"] == "r1"
        assert records[1]["record_id"] == "r2"

    def test_load_empty(self, backend):
        assert backend.load_provenance_records("nonexistent") == []

    def test_namespace_isolation(self, backend):
        backend.append_provenance_record("a", {"id": "1"})
        backend.append_provenance_record("b", {"id": "2"})
        assert len(backend.load_provenance_records("a")) == 1
        assert len(backend.load_provenance_records("b")) == 1

    def test_persistence_across_instances(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.append_provenance_record("l", {"record_id": "r1"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        records = b2.load_provenance_records("l")
        b2.close()
        assert len(records) == 1
        assert records[0]["record_id"] == "r1"


class TestSQLiteBackendExchange:
    def test_append_and_load(self, backend):
        backend.append_exchange_entry("log-1", {"entry_id": "e1"})
        backend.append_exchange_entry("log-1", {"entry_id": "e2"})
        entries = backend.load_exchange_entries("log-1")
        assert len(entries) == 2

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.append_exchange_entry("log", {"entry_id": "e1"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        assert len(b2.load_exchange_entries("log")) == 1
        b2.close()


class TestSQLiteBackendContracts:
    def test_save_and_load(self, backend):
        backend.save_contract("broker-1", "c1", {"status": "active"})
        backend.save_contract("broker-1", "c2", {"status": "pending"})
        contracts = backend.load_contracts("broker-1")
        assert len(contracts) == 2

    def test_overwrite(self, backend):
        backend.save_contract("b", "c1", {"status": "active"})
        backend.save_contract("b", "c1", {"status": "revoked"})
        contracts = backend.load_contracts("b")
        assert contracts["c1"]["status"] == "revoked"

    def test_remove(self, backend):
        backend.save_contract("b", "c1", {"status": "active"})
        backend.remove_contract("b", "c1")
        assert backend.load_contracts("b") == {}

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.save_contract("b", "c1", {"status": "active"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        assert b2.load_contracts("b")["c1"]["status"] == "active"
        b2.close()


class TestSQLiteBackendBaselines:
    def test_save_and_load(self, backend):
        backend.save_baseline("a", "agent-1", "m1", {"mean": 5.0})
        backend.save_baseline("a", "agent-1", "m2", {"mean": 10.0})
        baselines = backend.load_baselines("a")
        assert baselines["agent-1"]["m1"]["mean"] == 5.0
        assert baselines["agent-1"]["m2"]["mean"] == 10.0

    def test_overwrite(self, backend):
        backend.save_baseline("a", "actor", "m", {"mean": 1.0})
        backend.save_baseline("a", "actor", "m", {"mean": 2.0})
        baselines = backend.load_baselines("a")
        assert baselines["actor"]["m"]["mean"] == 2.0

    def test_remove_single(self, backend):
        backend.save_baseline("a", "actor", "m1", {"v": 1})
        backend.save_baseline("a", "actor", "m2", {"v": 2})
        backend.remove_baseline("a", "actor", "m1")
        baselines = backend.load_baselines("a")
        assert "m1" not in baselines.get("actor", {})
        assert baselines["actor"]["m2"]["v"] == 2

    def test_remove_all_for_actor(self, backend):
        backend.save_baseline("a", "actor", "m1", {"v": 1})
        backend.save_baseline("a", "actor", "m2", {"v": 2})
        backend.remove_baseline("a", "actor")
        baselines = backend.load_baselines("a")
        assert "actor" not in baselines

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.save_baseline("a", "agent", "m", {"mean": 42.0})
        b1.close()

        b2 = SQLiteBackend(db_path)
        assert b2.load_baselines("a")["agent"]["m"]["mean"] == 42.0
        b2.close()


class TestSQLiteBackendDrift:
    def test_append_and_load(self, backend):
        backend.append_drift_event("a", {"event_id": "d1"})
        backend.append_drift_event("a", {"event_id": "d2"})
        events = backend.load_drift_history("a")
        assert len(events) == 2

    def test_order_preserved(self, backend):
        for i in range(5):
            backend.append_drift_event("a", {"event_id": f"d{i}"})
        events = backend.load_drift_history("a")
        assert [e["event_id"] for e in events] == [f"d{i}" for i in range(5)]


class TestSQLiteBackendEscalations:
    def test_append_and_load(self, backend):
        backend.append_escalation("eng", {"action": "alert"})
        backend.append_escalation("eng", {"action": "suspend"})
        esc = backend.load_escalations("eng")
        assert len(esc) == 2

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.append_escalation("eng", {"action": "alert"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        assert len(b2.load_escalations("eng")) == 1
        b2.close()


class TestSQLiteBackendConsent:
    def test_full_graph_lifecycle(self, backend):
        backend.save_consent_node("g", "n1", {"type": "user"})
        backend.save_consent_node("g", "n2", {"type": "agent"})
        backend.save_consent_edge("g", "e1", {"source": "n1", "target": "n2"})
        backend.save_consent_group("g", "grp1", ["n1", "n2"])
        backend.append_consent_audit("g", {"action": "grant"})

        graph = backend.load_consent_graph("g")
        assert len(graph["nodes"]) == 2
        assert "e1" in graph["edges"]
        assert graph["groups"]["grp1"] == ["n1", "n2"]
        assert len(graph["audit_log"]) == 1

    def test_remove_operations(self, backend):
        backend.save_consent_node("g", "n1", {"type": "user"})
        backend.save_consent_edge("g", "e1", {"source": "n1"})
        backend.save_consent_group("g", "grp1", ["n1"])

        backend.remove_consent_node("g", "n1")
        backend.remove_consent_edge("g", "e1")
        backend.remove_consent_group("g", "grp1")

        graph = backend.load_consent_graph("g")
        assert graph["nodes"] == {}
        assert graph["edges"] == {}
        assert graph["groups"] == {}

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.save_consent_node("g", "n1", {"type": "user"})
        b1.save_consent_edge("g", "e1", {"src": "n1"})
        b1.append_consent_audit("g", {"action": "grant"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        graph = b2.load_consent_graph("g")
        b2.close()
        assert "n1" in graph["nodes"]
        assert "e1" in graph["edges"]
        assert len(graph["audit_log"]) == 1

    def test_empty_graph(self, backend):
        graph = backend.load_consent_graph("nonexistent")
        assert graph["nodes"] == {}
        assert graph["edges"] == {}
        assert graph["groups"] == {}
        assert graph["audit_log"] == []


class TestSQLiteBackendMarketplace:
    def test_server_lifecycle(self, backend):
        backend.save_server_registration("mp", "srv1", {"name": "Test"})
        backend.append_marketplace_audit("mp", {"action": "register"})
        mp = backend.load_marketplace("mp")
        assert "srv1" in mp["servers"]
        assert len(mp["audit_log"]) == 1

    def test_remove_registration(self, backend):
        backend.save_server_registration("mp", "srv1", {"name": "Test"})
        backend.remove_server_registration("mp", "srv1")
        mp = backend.load_marketplace("mp")
        assert "srv1" not in mp["servers"]

    def test_persistence(self, db_path):
        b1 = SQLiteBackend(db_path)
        b1.save_server_registration("mp", "srv1", {"name": "Test"})
        b1.append_marketplace_audit("mp", {"action": "register"})
        b1.close()

        b2 = SQLiteBackend(db_path)
        mp = b2.load_marketplace("mp")
        b2.close()
        assert "srv1" in mp["servers"]
        assert len(mp["audit_log"]) == 1

    def test_empty_marketplace(self, backend):
        mp = backend.load_marketplace("nonexistent")
        assert mp["servers"] == {}
        assert mp["audit_log"] == []


class TestSQLiteBackendSchemaCreation:
    def test_creates_tables_on_init(self, db_path):
        import sqlite3

        backend = SQLiteBackend(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        backend.close()

        expected = {
            "provenance_records",
            "exchange_entries",
            "contracts",
            "baselines",
            "drift_events",
            "escalations",
            "consent_nodes",
            "consent_edges",
            "consent_groups",
            "consent_audit_log",
            "server_registrations",
            "marketplace_audit_log",
        }
        assert expected.issubset(tables)

    def test_wal_mode(self, db_path):
        import sqlite3

        backend = SQLiteBackend(db_path)
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        backend.close()
        assert mode == "wal"
