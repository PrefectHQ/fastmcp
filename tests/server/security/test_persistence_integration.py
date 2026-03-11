"""End-to-end persistence integration tests.

Tests that components can persist state, be destroyed, then recreated
with the same backend and have identical state restored.
"""

import asyncio

from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.consent.models import ConsentNode, ConsentQuery, NodeType
from fastmcp.server.security.contracts.broker import ContextBroker
from fastmcp.server.security.contracts.exchange_log import (
    ExchangeEventType,
    ExchangeLog,
)
from fastmcp.server.security.contracts.schema import (
    ContractNegotiationRequest,
    ContractTerm,
)
from fastmcp.server.security.gateway.marketplace import Marketplace
from fastmcp.server.security.gateway.models import ServerCapability, TrustLevel
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.provenance.records import ProvenanceAction
from fastmcp.server.security.reflexive.analyzer import (
    BehavioralAnalyzer,
    EscalationEngine,
)
from fastmcp.server.security.reflexive.models import (
    DriftEvent,
    DriftSeverity,
    DriftType,
    EscalationAction,
    EscalationRule,
)
from fastmcp.server.security.storage.memory import MemoryBackend
from fastmcp.server.security.storage.sqlite import SQLiteBackend

# ── ProvenanceLedger ──────────────────────────────────────────────


class TestProvenanceLedgerPersistence:
    def test_memory_backend_persist_and_reload(self):
        backend = MemoryBackend()
        ledger1 = ProvenanceLedger("test-ledger", backend=backend)
        r1 = ledger1.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calc",
            input_data={"expression": "2+2"},
        )
        r2 = ledger1.record(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="agent-2",
            resource_id="db",
        )
        original_count = ledger1.record_count
        original_root = ledger1.root_hash
        assert ledger1.verify_chain()
        assert ledger1.verify_tree()

        # Create new ledger with same backend
        ledger2 = ProvenanceLedger("test-ledger", backend=backend)
        assert ledger2.record_count == original_count
        assert ledger2.root_hash == original_root
        assert ledger2.verify_chain()
        assert ledger2.verify_tree()
        assert ledger2.get_record(r1.record_id) is not None
        assert ledger2.get_record(r2.record_id) is not None

    def test_sqlite_backend_persist_and_reload(self, tmp_path):
        db = str(tmp_path / "test.db")
        backend1 = SQLiteBackend(db)
        ledger1 = ProvenanceLedger("test-ledger", backend=backend1)
        ledger1.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calc",
        )
        ledger1.record(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="agent-1",
            resource_id="db",
        )
        count = ledger1.record_count
        root = ledger1.root_hash
        digest = ledger1.get_chain_digest()
        backend1.close()

        # Reopen with fresh backend instance
        backend2 = SQLiteBackend(db)
        ledger2 = ProvenanceLedger("test-ledger", backend=backend2)
        assert ledger2.record_count == count
        assert ledger2.root_hash == root
        assert ledger2.get_chain_digest() == digest
        assert ledger2.verify_chain()
        assert ledger2.verify_tree()
        backend2.close()

    def test_new_records_after_reload(self):
        backend = MemoryBackend()
        ledger1 = ProvenanceLedger("l", backend=backend)
        ledger1.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="a",
            resource_id="r",
        )

        ledger2 = ProvenanceLedger("l", backend=backend)
        ledger2.record(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="a",
            resource_id="r2",
        )
        assert ledger2.record_count == 2
        assert ledger2.verify_chain()
        assert ledger2.verify_tree()


# ── ExchangeLog ──────────────────────────────────────────────────


class TestExchangeLogPersistence:
    def test_persist_and_reload(self):
        backend = MemoryBackend()
        log1 = ExchangeLog("test-log", backend=backend)
        log1.record(
            session_id="sess-1",
            event_type=ExchangeEventType.SESSION_STARTED,
            actor_id="server-1",
            data={"agent_id": "agent-1"},
        )
        log1.record(
            session_id="sess-1",
            event_type=ExchangeEventType.ACCEPTED,
            actor_id="server-1",
        )
        assert log1.entry_count == 2
        assert log1.verify_chain("sess-1")

        # Reload
        log2 = ExchangeLog("test-log", backend=backend)
        assert log2.entry_count == 2
        assert log2.session_count == 1
        assert log2.verify_chain("sess-1")
        entries = log2.get_session_entries("sess-1")
        assert len(entries) == 2
        assert entries[0].event_type == ExchangeEventType.SESSION_STARTED

    def test_sqlite_persist(self, tmp_path):
        db = str(tmp_path / "test.db")
        b1 = SQLiteBackend(db)
        log1 = ExchangeLog("log", backend=b1)
        log1.record(
            session_id="s",
            event_type=ExchangeEventType.SESSION_STARTED,
            actor_id="srv",
        )
        b1.close()

        b2 = SQLiteBackend(db)
        log2 = ExchangeLog("log", backend=b2)
        assert log2.entry_count == 1
        assert log2.verify_chain("s")
        b2.close()


# ── ContextBroker ────────────────────────────────────────────────


class TestContextBrokerPersistence:
    def test_contract_persists_after_negotiation(self):
        backend = MemoryBackend()
        broker1 = ContextBroker(
            server_id="srv-1",
            broker_id="test-broker",
            backend=backend,
        )
        request = ContractNegotiationRequest(
            agent_id="agent-1",
            proposed_terms=[
                ContractTerm(description="Read access"),
            ],
        )
        response = asyncio.get_event_loop().run_until_complete(
            broker1.negotiate(request)
        )
        assert response.contract is not None
        contract_id = response.contract.contract_id

        # Reload broker
        broker2 = ContextBroker(
            server_id="srv-1",
            broker_id="test-broker",
            backend=backend,
        )
        loaded = broker2.get_contract(contract_id)
        assert loaded is not None
        assert loaded.agent_id == "agent-1"
        assert loaded.is_valid()


# ── BehavioralAnalyzer ───────────────────────────────────────────


class TestBehavioralAnalyzerPersistence:
    def test_baselines_persist(self):
        backend = MemoryBackend()
        analyzer1 = BehavioralAnalyzer(analyzer_id="test-analyzer", backend=backend)
        for v in [5.0, 6.0, 7.0, 4.0, 5.5, 6.5, 5.2, 4.8, 5.1, 5.3]:
            analyzer1.observe("agent-1", "calls_per_min", v)

        baseline = analyzer1.get_baseline("agent-1", "calls_per_min")
        assert baseline is not None
        original_mean = baseline.mean
        original_count = baseline.sample_count

        # Reload
        analyzer2 = BehavioralAnalyzer(analyzer_id="test-analyzer", backend=backend)
        restored = analyzer2.get_baseline("agent-1", "calls_per_min")
        assert restored is not None
        assert restored.sample_count == original_count
        assert abs(restored.mean - original_mean) < 1e-9

    def test_drift_history_persists(self):
        backend = MemoryBackend()
        analyzer1 = BehavioralAnalyzer(
            analyzer_id="test", backend=backend, min_samples=5
        )
        # Build baseline with enough samples (compute_deviation needs >= 5)
        for v in [5.0, 5.1, 4.9, 5.0, 5.1, 4.9, 5.0, 5.1, 4.9, 5.0]:
            analyzer1.observe("a", "m", v)
        # Trigger drift with a massive spike (well beyond 5 sigma)
        events = analyzer1.observe("a", "m", 500.0)
        assert len(events) > 0, "Expected drift events but got none"

        # Reload
        analyzer2 = BehavioralAnalyzer(
            analyzer_id="test", backend=backend, min_samples=5
        )
        assert analyzer2.total_drift_count > 0

    def test_sqlite_baselines(self, tmp_path):
        db = str(tmp_path / "test.db")
        b1 = SQLiteBackend(db)
        a1 = BehavioralAnalyzer(analyzer_id="a", backend=b1)
        for v in [1.0, 2.0, 3.0]:
            a1.observe("agent", "metric", v)
        b1.close()

        b2 = SQLiteBackend(db)
        a2 = BehavioralAnalyzer(analyzer_id="a", backend=b2)
        baseline = a2.get_baseline("agent", "metric")
        assert baseline is not None
        assert baseline.sample_count == 3
        b2.close()


# ── EscalationEngine ─────────────────────────────────────────────


class TestEscalationEnginePersistence:
    def test_history_persists(self):
        backend = MemoryBackend()
        rules = [
            EscalationRule(
                min_severity=DriftSeverity.LOW,
                action=EscalationAction.ALERT,
            )
        ]
        engine1 = EscalationEngine(
            rules=rules, engine_id="test-engine", backend=backend
        )
        event = DriftEvent(
            drift_type=DriftType.FREQUENCY_SPIKE,
            severity=DriftSeverity.HIGH,
            actor_id="agent-1",
        )
        triggered = engine1.evaluate(event)
        assert len(triggered) > 0
        assert engine1.escalation_count == 1

        # Reload
        engine2 = EscalationEngine(
            rules=rules, engine_id="test-engine", backend=backend
        )
        assert engine2.escalation_count == 1


# ── ConsentGraph ─────────────────────────────────────────────────


class TestConsentGraphPersistence:
    def test_grant_persists(self):
        backend = MemoryBackend()
        graph1 = ConsentGraph(graph_id="test-graph", backend=backend)
        graph1.add_node(
            ConsentNode(node_id="source-1", node_type=NodeType.AGENT, label="Source")
        )
        graph1.add_node(
            ConsentNode(node_id="target-1", node_type=NodeType.RESOURCE, label="Target")
        )
        edge = graph1.grant(
            source_id="source-1",
            target_id="target-1",
            scopes={"read", "execute"},
            granted_by="source-1",
        )

        # Reload
        graph2 = ConsentGraph(graph_id="test-graph", backend=backend)
        assert graph2.get_node("source-1") is not None
        assert graph2.get_node("target-1") is not None
        result = graph2.evaluate(
            ConsentQuery(source_id="source-1", target_id="target-1", scope="read")
        )
        assert result.granted

    def test_revoke_persists(self):
        backend = MemoryBackend()
        graph1 = ConsentGraph(graph_id="g", backend=backend)
        graph1.add_node(ConsentNode(node_id="u", node_type=NodeType.AGENT))
        graph1.add_node(ConsentNode(node_id="a", node_type=NodeType.RESOURCE))
        edge = graph1.grant(
            source_id="u",
            target_id="a",
            scopes={"read"},
            granted_by="u",
        )
        graph1.revoke(edge.edge_id)

        # Reload
        graph2 = ConsentGraph(graph_id="g", backend=backend)
        result = graph2.evaluate(
            ConsentQuery(source_id="u", target_id="a", scope="read")
        )
        assert not result.granted

    def test_groups_persist(self):
        backend = MemoryBackend()
        graph1 = ConsentGraph(graph_id="g", backend=backend)
        graph1.add_node(ConsentNode(node_id="u", node_type=NodeType.AGENT))
        graph1.add_to_group("team-a", "u")

        graph2 = ConsentGraph(graph_id="g", backend=backend)
        members = graph2.get_group_members("team-a")
        assert "u" in members

    def test_sqlite_consent(self, tmp_path):
        db = str(tmp_path / "test.db")
        b1 = SQLiteBackend(db)
        g1 = ConsentGraph(graph_id="g", backend=b1)
        g1.add_node(
            ConsentNode(node_id="source", node_type=NodeType.AGENT, label="Source")
        )
        g1.add_node(
            ConsentNode(node_id="target", node_type=NodeType.RESOURCE, label="Target")
        )
        g1.grant(
            source_id="source",
            target_id="target",
            scopes={"read"},
            granted_by="source",
        )
        b1.close()

        b2 = SQLiteBackend(db)
        g2 = ConsentGraph(graph_id="g", backend=b2)
        assert g2.get_node("source") is not None
        assert g2.get_node("target") is not None
        result = g2.evaluate(
            ConsentQuery(source_id="source", target_id="target", scope="read")
        )
        assert result.granted
        b2.close()


# ── Marketplace ──────────────────────────────────────────────────


class TestMarketplacePersistence:
    def test_registration_persists(self):
        backend = MemoryBackend()
        mp1 = Marketplace("test-mp", backend=backend)
        reg = mp1.register(
            name="Secure Server",
            endpoint="https://example.com",
            capabilities={ServerCapability.POLICY_ENGINE},
            trust_level=TrustLevel.SELF_CERTIFIED,
        )

        # Reload
        mp2 = Marketplace("test-mp", backend=backend)
        loaded = mp2.get(reg.server_id)
        assert loaded is not None
        assert loaded.name == "Secure Server"
        assert loaded.trust_level == TrustLevel.SELF_CERTIFIED
        assert ServerCapability.POLICY_ENGINE in loaded.capabilities

    def test_unregister_persists(self):
        backend = MemoryBackend()
        mp1 = Marketplace("mp", backend=backend)
        reg = mp1.register(name="S", endpoint="https://x.com")
        mp1.unregister(reg.server_id)

        mp2 = Marketplace("mp", backend=backend)
        assert mp2.get(reg.server_id) is None
        assert mp2.server_count == 0

    def test_trust_update_persists(self):
        backend = MemoryBackend()
        mp1 = Marketplace("mp", backend=backend)
        reg = mp1.register(name="S", endpoint="https://x.com")
        mp1.update_trust_level(reg.server_id, TrustLevel.COMMUNITY_VERIFIED)

        mp2 = Marketplace("mp", backend=backend)
        loaded = mp2.get(reg.server_id)
        assert loaded is not None
        assert loaded.trust_level == TrustLevel.COMMUNITY_VERIFIED

    def test_sqlite_marketplace(self, tmp_path):
        db = str(tmp_path / "test.db")
        b1 = SQLiteBackend(db)
        mp1 = Marketplace("mp", backend=b1)
        reg = mp1.register(
            name="Test",
            endpoint="https://test.com",
            capabilities={ServerCapability.CONSENT_GRAPH},
        )
        sid = reg.server_id
        b1.close()

        b2 = SQLiteBackend(db)
        mp2 = Marketplace("mp", backend=b2)
        loaded = mp2.get(sid)
        assert loaded is not None
        assert loaded.name == "Test"
        assert ServerCapability.CONSENT_GRAPH in loaded.capabilities
        b2.close()
