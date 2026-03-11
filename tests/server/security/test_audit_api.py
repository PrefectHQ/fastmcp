"""Tests for the AuditAPI."""

from __future__ import annotations

from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.gateway.audit import AuditAPI
from fastmcp.server.security.gateway.models import (
    AuditQuery,
    AuditQueryType,
    HealthStatus,
)
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.policy.provider import AllowAllPolicy
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.provenance.records import ProvenanceAction
from fastmcp.server.security.reflexive.analyzer import BehavioralAnalyzer


class TestAuditAPIProvenance:
    def test_query_provenance_no_ledger(self):
        api = AuditAPI()
        result = api.query(AuditQuery(query_type=AuditQueryType.PROVENANCE))
        assert "error" in result.metadata

    def test_query_provenance_empty(self):
        ledger = ProvenanceLedger()
        api = AuditAPI(provenance_ledger=ledger)
        result = api.query(AuditQuery(query_type=AuditQueryType.PROVENANCE))
        assert result.total_count == 0
        assert result.records == []

    def test_query_provenance_with_records(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="tool-a",
        )
        ledger.record(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="agent-2",
            resource_id="data-b",
        )
        api = AuditAPI(provenance_ledger=ledger)
        result = api.query(AuditQuery(query_type=AuditQueryType.PROVENANCE))
        assert result.total_count == 2
        assert len(result.records) == 2

    def test_query_provenance_with_actor_filter(self):
        ledger = ProvenanceLedger()
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="tool-a",
        )
        ledger.record(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-2",
            resource_id="tool-b",
        )
        api = AuditAPI(provenance_ledger=ledger)
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.PROVENANCE,
                actor_id="agent-1",
            )
        )
        assert len(result.records) == 1

    def test_query_provenance_has_more(self):
        ledger = ProvenanceLedger()
        for i in range(5):
            ledger.record(
                action=ProvenanceAction.TOOL_CALLED,
                actor_id="a",
                resource_id=f"r-{i}",
            )
        api = AuditAPI(provenance_ledger=ledger)
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.PROVENANCE,
                limit=3,
            )
        )
        assert len(result.records) == 3
        assert result.has_more


class TestAuditAPIDrift:
    def test_query_drift_no_analyzer(self):
        api = AuditAPI()
        result = api.query(AuditQuery(query_type=AuditQueryType.DRIFT))
        assert "error" in result.metadata

    def test_query_drift_no_events(self):
        analyzer = BehavioralAnalyzer()
        api = AuditAPI(behavioral_analyzer=analyzer)
        result = api.query(AuditQuery(query_type=AuditQueryType.DRIFT))
        assert result.total_count == 0

    def test_query_drift_with_events(self):
        analyzer = BehavioralAnalyzer(min_samples=5)
        # Build baseline then trigger drift
        for v in [10, 10, 10, 10, 10, 11, 9, 10]:
            analyzer.observe("a1", "calls", float(v))
        analyzer.observe("a1", "calls", 100.0)

        api = AuditAPI(behavioral_analyzer=analyzer)
        result = api.query(AuditQuery(query_type=AuditQueryType.DRIFT))
        assert result.total_count >= 1
        assert len(result.records) >= 1
        assert result.records[0]["actor_id"] == "a1"


class TestAuditAPIConsent:
    def test_query_consent_no_graph(self):
        api = AuditAPI()
        result = api.query(AuditQuery(query_type=AuditQueryType.CONSENT))
        assert "error" in result.metadata

    def test_query_consent_with_log(self):
        graph = ConsentGraph()
        graph.grant("owner", "agent", {"read"})
        api = AuditAPI(consent_graph=graph)
        result = api.query(AuditQuery(query_type=AuditQueryType.CONSENT))
        assert result.total_count == 1
        assert result.records[0]["action"] == "grant"

    def test_query_consent_with_actor_filter(self):
        graph = ConsentGraph()
        graph.grant("owner", "agent-1", {"read"})
        graph.grant("owner", "agent-2", {"write"})
        api = AuditAPI(consent_graph=graph)
        result = api.query(
            AuditQuery(
                query_type=AuditQueryType.CONSENT,
                actor_id="agent-1",
            )
        )
        assert len(result.records) == 1


class TestAuditAPIPolicy:
    def test_query_policy_no_engine(self):
        api = AuditAPI()
        result = api.query(AuditQuery(query_type=AuditQueryType.POLICY))
        assert "error" in result.metadata

    def test_query_policy_summary(self):
        engine = PolicyEngine(providers=[AllowAllPolicy()])
        api = AuditAPI(policy_engine=engine)
        result = api.query(AuditQuery(query_type=AuditQueryType.POLICY))
        assert len(result.records) == 1
        assert result.records[0]["provider_count"] == 1


class TestAuditAPIStatus:
    def test_status_healthy(self):
        ledger = ProvenanceLedger()
        api = AuditAPI(provenance_ledger=ledger)
        status = api.get_status()
        assert status.status == HealthStatus.HEALTHY
        assert "provenance" in status.layers

    def test_status_with_all_layers(self):
        api = AuditAPI(
            provenance_ledger=ProvenanceLedger(),
            behavioral_analyzer=BehavioralAnalyzer(),
            consent_graph=ConsentGraph(),
            policy_engine=PolicyEngine(providers=[AllowAllPolicy()]),
        )
        status = api.get_status()
        assert "provenance" in status.layers
        assert "reflexive" in status.layers
        assert "consent" in status.layers
        assert "policy" in status.layers

    def test_query_count_tracked(self):
        api = AuditAPI()
        assert api.query_count == 0
        api.query(AuditQuery(query_type=AuditQueryType.PROVENANCE))
        api.query(AuditQuery(query_type=AuditQueryType.DRIFT))
        assert api.query_count == 2

    def test_contract_query_informational(self):
        api = AuditAPI()
        result = api.query(AuditQuery(query_type=AuditQueryType.CONTRACT))
        assert "info" in result.metadata
