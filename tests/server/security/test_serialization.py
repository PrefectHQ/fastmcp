"""Round-trip serialization tests for all SecureMCP data models."""

from datetime import datetime, timezone

from fastmcp.server.security.consent.models import (
    ConsentCondition,
    ConsentEdge,
    ConsentNode,
    ConsentStatus,
    NodeType,
)
from fastmcp.server.security.contracts.exchange_log import (
    ExchangeEventType,
    ExchangeLogEntry,
)
from fastmcp.server.security.contracts.schema import (
    Contract,
    ContractStatus,
    ContractTerm,
    TermType,
)
from fastmcp.server.security.gateway.models import (
    ServerCapability,
    ServerRegistration,
    TrustLevel,
)
from fastmcp.server.security.provenance.records import (
    ProvenanceAction,
    ProvenanceRecord,
)
from fastmcp.server.security.reflexive.models import (
    BehavioralBaseline,
    DriftEvent,
    DriftSeverity,
    DriftType,
    EscalationAction,
    EscalationRule,
)
from fastmcp.server.security.storage.serialization import (
    baseline_from_dict,
    baseline_to_dict,
    consent_condition_from_dict,
    consent_condition_to_dict,
    consent_edge_from_dict,
    consent_edge_to_dict,
    consent_node_from_dict,
    consent_node_to_dict,
    contract_from_dict,
    contract_term_from_dict,
    contract_term_to_dict,
    contract_to_dict,
    drift_event_from_dict,
    drift_event_to_dict,
    escalation_rule_from_dict,
    escalation_rule_to_dict,
    exchange_entry_from_dict,
    exchange_entry_to_dict,
    provenance_record_from_dict,
    provenance_record_to_dict,
    server_registration_from_dict,
    server_registration_to_dict,
)


class TestProvenanceRecordSerialization:
    def test_round_trip(self):
        record = ProvenanceRecord(
            action=ProvenanceAction.TOOL_CALLED,
            actor_id="agent-1",
            resource_id="calculator",
            input_hash="abc123",
            output_hash="def456",
            metadata={"key": "value"},
            previous_hash="genesis",
            contract_id="contract-1",
            session_id="session-1",
        )
        data = provenance_record_to_dict(record)
        restored = provenance_record_from_dict(data)

        assert restored.record_id == record.record_id
        assert restored.action == ProvenanceAction.TOOL_CALLED
        assert restored.actor_id == "agent-1"
        assert restored.resource_id == "calculator"
        assert restored.input_hash == "abc123"
        assert restored.output_hash == "def456"
        assert restored.metadata == {"key": "value"}
        assert restored.previous_hash == "genesis"
        assert restored.contract_id == "contract-1"
        assert restored.session_id == "session-1"

    def test_hash_preserved(self):
        record = ProvenanceRecord(
            action=ProvenanceAction.RESOURCE_READ,
            actor_id="a",
        )
        data = provenance_record_to_dict(record)
        restored = provenance_record_from_dict(data)
        assert restored.compute_hash() == record.compute_hash()


class TestExchangeLogEntrySerialization:
    def test_round_trip(self):
        ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        entry = ExchangeLogEntry(
            entry_id="exlog-000001",
            session_id="sess-1",
            event_type=ExchangeEventType.SESSION_STARTED,
            timestamp=ts,
            actor_id="server-1",
            data={"agent_id": "agent-1"},
            data_hash="somehash",
            previous_hash="genesis",
        )
        data = exchange_entry_to_dict(entry)
        restored = exchange_entry_from_dict(data)

        assert restored.entry_id == "exlog-000001"
        assert restored.session_id == "sess-1"
        assert restored.event_type == ExchangeEventType.SESSION_STARTED
        assert restored.timestamp == ts
        assert restored.actor_id == "server-1"
        assert restored.data == {"agent_id": "agent-1"}
        assert restored.data_hash == "somehash"
        assert restored.previous_hash == "genesis"

    def test_hash_preserved(self):
        entry = ExchangeLogEntry(
            entry_id="exlog-000002",
            session_id="s",
            event_type=ExchangeEventType.ACCEPTED,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            actor_id="a",
        )
        data = exchange_entry_to_dict(entry)
        restored = exchange_entry_from_dict(data)
        assert restored.compute_hash() == entry.compute_hash()


class TestContractTermSerialization:
    def test_round_trip(self):
        term = ContractTerm(
            term_type=TermType.ACCESS_CONTROL,
            description="Read-only access",
            constraint={"read_only": True},
            required=True,
            metadata={"priority": "high"},
        )
        data = contract_term_to_dict(term)
        restored = contract_term_from_dict(data)

        assert restored.term_id == term.term_id
        assert restored.term_type == TermType.ACCESS_CONTROL
        assert restored.description == "Read-only access"
        assert restored.constraint == {"read_only": True}
        assert restored.required is True
        assert restored.metadata == {"priority": "high"}


class TestContractSerialization:
    def test_round_trip(self):
        terms = [
            ContractTerm(
                term_type=TermType.ACCESS_CONTROL,
                description="test",
            )
        ]
        contract = Contract(
            session_id="sess-1",
            server_id="srv-1",
            agent_id="agent-1",
            terms=terms,
            status=ContractStatus.ACTIVE,
        )
        contract.signatures["srv-1"] = "sig-abc"

        data = contract_to_dict(contract)
        restored = contract_from_dict(data)

        assert restored.contract_id == contract.contract_id
        assert restored.session_id == "sess-1"
        assert restored.server_id == "srv-1"
        assert restored.agent_id == "agent-1"
        assert restored.status == ContractStatus.ACTIVE
        assert len(restored.terms) == 1
        assert restored.terms[0].term_type == TermType.ACCESS_CONTROL
        assert restored.signatures == {"srv-1": "sig-abc"}


class TestBaselineSerialization:
    def test_round_trip(self):
        baseline = BehavioralBaseline(
            metric_name="calls_per_min",
            actor_id="agent-1",
        )
        for v in [5.0, 6.0, 7.0, 4.0, 5.5]:
            baseline.update(v)

        data = baseline_to_dict(baseline)
        restored = baseline_from_dict(data)

        assert restored.metric_name == "calls_per_min"
        assert restored.actor_id == "agent-1"
        assert restored.sample_count == baseline.sample_count
        assert abs(restored.mean - baseline.mean) < 1e-9
        assert abs(restored.variance - baseline.variance) < 1e-9
        assert restored.min_value == baseline.min_value
        assert restored.max_value == baseline.max_value

    def test_empty_baseline(self):
        baseline = BehavioralBaseline(metric_name="m", actor_id="a")
        data = baseline_to_dict(baseline)
        restored = baseline_from_dict(data)
        assert restored.sample_count == 0
        assert restored.mean == 0.0


class TestDriftEventSerialization:
    def test_round_trip(self):
        event = DriftEvent(
            drift_type=DriftType.FREQUENCY_SPIKE,
            severity=DriftSeverity.HIGH,
            actor_id="agent-1",
            description="Big spike",
            observed_value=50.0,
            baseline_value=5.0,
            deviation=4.5,
            metadata={"metric_name": "calls_per_min"},
        )
        data = drift_event_to_dict(event)
        restored = drift_event_from_dict(data)

        assert restored.event_id == event.event_id
        assert restored.drift_type == DriftType.FREQUENCY_SPIKE
        assert restored.severity == DriftSeverity.HIGH
        assert restored.actor_id == "agent-1"
        assert restored.description == "Big spike"
        assert restored.observed_value == 50.0
        assert restored.baseline_value == 5.0
        assert restored.deviation == 4.5
        assert restored.metadata == {"metric_name": "calls_per_min"}


class TestEscalationRuleSerialization:
    def test_round_trip(self):
        rule = EscalationRule(
            min_severity=DriftSeverity.HIGH,
            drift_types=[DriftType.FREQUENCY_SPIKE, DriftType.SCOPE_EXPANSION],
            action=EscalationAction.SUSPEND_AGENT,
            threshold_count=3,
            cooldown_seconds=120.0,
            enabled=True,
            metadata={"note": "test"},
        )
        data = escalation_rule_to_dict(rule)
        restored = escalation_rule_from_dict(data)

        assert restored.rule_id == rule.rule_id
        assert restored.min_severity == DriftSeverity.HIGH
        assert set(restored.drift_types) == {
            DriftType.FREQUENCY_SPIKE,
            DriftType.SCOPE_EXPANSION,
        }
        assert restored.action == EscalationAction.SUSPEND_AGENT
        assert restored.threshold_count == 3
        assert restored.cooldown_seconds == 120.0
        assert restored.enabled is True


class TestConsentNodeSerialization:
    def test_round_trip(self):
        node = ConsentNode(
            node_id="agent-1",
            node_type=NodeType.AGENT,
            label="Bot",
            metadata={"role": "assistant"},
        )
        data = consent_node_to_dict(node)
        restored = consent_node_from_dict(data)

        assert restored.node_id == "agent-1"
        assert restored.node_type == NodeType.AGENT
        assert restored.label == "Bot"
        assert restored.metadata == {"role": "assistant"}


class TestConsentConditionSerialization:
    def test_round_trip(self):
        cond = ConsentCondition(
            expression="time.hour >= 9 and time.hour < 17",
            description="Business hours only",
            metadata={"tz": "UTC"},
        )
        data = consent_condition_to_dict(cond)
        restored = consent_condition_from_dict(data)

        assert restored.condition_id == cond.condition_id
        assert restored.expression == "time.hour >= 9 and time.hour < 17"
        assert restored.description == "Business hours only"


class TestConsentEdgeSerialization:
    def test_round_trip(self):
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        edge = ConsentEdge(
            source_id="source-1",
            target_id="target-1",
            scopes={"read", "execute"},
            status=ConsentStatus.ACTIVE,
            conditions=[
                ConsentCondition(expression="true", description="always")
            ],
            granted_at=ts,
            granted_by="source-1",
            delegatable=True,
            max_delegation_depth=2,
            delegation_depth=0,
            metadata={"reason": "testing"},
        )
        data = consent_edge_to_dict(edge)
        restored = consent_edge_from_dict(data)

        assert restored.edge_id == edge.edge_id
        assert restored.source_id == "source-1"
        assert restored.target_id == "target-1"
        assert restored.scopes == {"read", "execute"}
        assert restored.status == ConsentStatus.ACTIVE
        assert len(restored.conditions) == 1
        assert restored.granted_at == ts
        assert restored.granted_by == "source-1"
        assert restored.delegatable is True
        assert restored.max_delegation_depth == 2
        assert restored.metadata == {"reason": "testing"}

    def test_edge_with_expiry(self):
        exp = datetime(2025, 12, 31, tzinfo=timezone.utc)
        edge = ConsentEdge(
            source_id="a",
            target_id="b",
            scopes={"read"},
            expires_at=exp,
        )
        data = consent_edge_to_dict(edge)
        restored = consent_edge_from_dict(data)
        assert restored.expires_at == exp

    def test_edge_without_expiry(self):
        edge = ConsentEdge(
            source_id="a",
            target_id="b",
            scopes={"read"},
        )
        data = consent_edge_to_dict(edge)
        restored = consent_edge_from_dict(data)
        assert restored.expires_at is None


class TestServerRegistrationSerialization:
    def test_round_trip(self):
        reg = ServerRegistration(
            name="Test Server",
            endpoint="https://test.example.com",
            capabilities={
                ServerCapability.POLICY_ENGINE,
                ServerCapability.PROVENANCE_LEDGER,
            },
            trust_level=TrustLevel.SELF_CERTIFIED,
            version="1.0.0",
            description="A test server",
            tags={"test", "secure"},
            metadata={"owner": "test-user"},
        )
        data = server_registration_to_dict(reg)
        restored = server_registration_from_dict(data)

        assert restored.server_id == reg.server_id
        assert restored.name == "Test Server"
        assert restored.endpoint == "https://test.example.com"
        assert restored.capabilities == {
            ServerCapability.POLICY_ENGINE,
            ServerCapability.PROVENANCE_LEDGER,
        }
        assert restored.trust_level == TrustLevel.SELF_CERTIFIED
        assert restored.version == "1.0.0"
        assert restored.description == "A test server"
        assert restored.tags == {"test", "secure"}
        assert restored.metadata == {"owner": "test-user"}
