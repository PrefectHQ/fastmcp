"""Serialization utilities for SecureMCP data models.

Provides to_dict/from_dict round-tripping for frozen dataclasses,
mutable dataclasses, and enum types used across all security layers.
All serialization uses JSON-safe dicts (no pickle).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.consent.models import (
    ConsentCondition,
    ConsentEdge,
    ConsentNode,
    ConsentScope,
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


# ── Datetime helpers ──────────────────────────────────────────────


def _dt_to_str(dt: datetime) -> str:
    """Convert datetime to ISO 8601 string."""
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    """Parse ISO 8601 string to datetime (UTC)."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── ProvenanceRecord ──────────────────────────────────────────────


def provenance_record_to_dict(record: ProvenanceRecord) -> dict[str, Any]:
    """Serialize a ProvenanceRecord to a JSON-safe dict."""
    return record.to_dict()


def provenance_record_from_dict(data: dict[str, Any]) -> ProvenanceRecord:
    """Deserialize a ProvenanceRecord from a dict."""
    return ProvenanceRecord(
        record_id=data["record_id"],
        action=ProvenanceAction(data["action"]),
        actor_id=data.get("actor_id", ""),
        resource_id=data.get("resource_id", ""),
        timestamp=_str_to_dt(data["timestamp"]),
        input_hash=data.get("input_hash", ""),
        output_hash=data.get("output_hash", ""),
        metadata=data.get("metadata", {}),
        previous_hash=data.get("previous_hash", ""),
        contract_id=data.get("contract_id", ""),
        session_id=data.get("session_id", ""),
    )


# ── ExchangeLogEntry ─────────────────────────────────────────────


def exchange_entry_to_dict(entry: ExchangeLogEntry) -> dict[str, Any]:
    """Serialize an ExchangeLogEntry to a JSON-safe dict."""
    return {
        "entry_id": entry.entry_id,
        "session_id": entry.session_id,
        "event_type": entry.event_type.value,
        "timestamp": _dt_to_str(entry.timestamp),
        "actor_id": entry.actor_id,
        "data": entry.data,
        "data_hash": entry.data_hash,
        "previous_hash": entry.previous_hash,
    }


def exchange_entry_from_dict(data: dict[str, Any]) -> ExchangeLogEntry:
    """Deserialize an ExchangeLogEntry from a dict."""
    return ExchangeLogEntry(
        entry_id=data["entry_id"],
        session_id=data["session_id"],
        event_type=ExchangeEventType(data["event_type"]),
        timestamp=_str_to_dt(data["timestamp"]),
        actor_id=data.get("actor_id", ""),
        data=data.get("data", {}),
        data_hash=data.get("data_hash", ""),
        previous_hash=data.get("previous_hash", ""),
    )


# ── Contract + ContractTerm ───────────────────────────────────────


def contract_term_to_dict(term: ContractTerm) -> dict[str, Any]:
    """Serialize a ContractTerm to a JSON-safe dict."""
    return {
        "term_id": term.term_id,
        "term_type": term.term_type.value,
        "description": term.description,
        "constraint": term.constraint,
        "required": term.required,
        "metadata": term.metadata,
    }


def contract_term_from_dict(data: dict[str, Any]) -> ContractTerm:
    """Deserialize a ContractTerm from a dict."""
    return ContractTerm(
        term_id=data.get("term_id", ""),
        term_type=TermType(data["term_type"]),
        description=data.get("description", ""),
        constraint=data.get("constraint", {}),
        required=data.get("required", False),
        metadata=data.get("metadata", {}),
    )


def contract_to_dict(contract: Contract) -> dict[str, Any]:
    """Serialize a Contract to a JSON-safe dict."""
    d = contract.to_dict()
    # Add signatures which to_dict() omits
    d["signatures"] = dict(contract.signatures)
    return d


def contract_from_dict(data: dict[str, Any]) -> Contract:
    """Deserialize a Contract from a dict."""
    terms = [contract_term_from_dict(t) for t in data.get("terms", [])]
    expires_at = _str_to_dt(data["expires_at"]) if data.get("expires_at") else None

    c = Contract(
        contract_id=data["contract_id"],
        session_id=data.get("session_id", ""),
        server_id=data.get("server_id", ""),
        agent_id=data.get("agent_id", ""),
        terms=terms,
        status=ContractStatus(data["status"]),
        created_at=_str_to_dt(data["created_at"]),
        expires_at=expires_at,
        version=data.get("version", 1),
        parent_id=data.get("parent_id"),
        metadata=data.get("metadata", {}),
    )
    c.signatures.update(data.get("signatures", {}))
    return c


# ── BehavioralBaseline ────────────────────────────────────────────


def baseline_to_dict(baseline: BehavioralBaseline) -> dict[str, Any]:
    """Serialize a BehavioralBaseline to a JSON-safe dict."""
    return {
        "metric_name": baseline.metric_name,
        "actor_id": baseline.actor_id,
        "sample_count": baseline.sample_count,
        "mean": baseline.mean,
        "variance": baseline.variance,
        "min_value": baseline.min_value if baseline.min_value != float("inf") else None,
        "max_value": baseline.max_value if baseline.max_value != float("-inf") else None,
        "last_updated": _dt_to_str(baseline.last_updated),
    }


def baseline_from_dict(data: dict[str, Any]) -> BehavioralBaseline:
    """Deserialize a BehavioralBaseline from a dict."""
    return BehavioralBaseline(
        metric_name=data.get("metric_name", ""),
        actor_id=data.get("actor_id", ""),
        sample_count=data.get("sample_count", 0),
        mean=data.get("mean", 0.0),
        variance=data.get("variance", 0.0),
        min_value=data["min_value"] if data.get("min_value") is not None else float("inf"),
        max_value=data["max_value"] if data.get("max_value") is not None else float("-inf"),
        last_updated=_str_to_dt(data["last_updated"]) if data.get("last_updated") else datetime.now(timezone.utc),
    )


# ── DriftEvent ────────────────────────────────────────────────────


def drift_event_to_dict(event: DriftEvent) -> dict[str, Any]:
    """Serialize a DriftEvent to a JSON-safe dict."""
    return {
        "event_id": event.event_id,
        "drift_type": event.drift_type.value,
        "severity": event.severity.value,
        "actor_id": event.actor_id,
        "description": event.description,
        "observed_value": event.observed_value,
        "baseline_value": event.baseline_value,
        "deviation": event.deviation,
        "timestamp": _dt_to_str(event.timestamp),
        "metadata": event.metadata,
    }


def drift_event_from_dict(data: dict[str, Any]) -> DriftEvent:
    """Deserialize a DriftEvent from a dict."""
    return DriftEvent(
        event_id=data["event_id"],
        drift_type=DriftType(data["drift_type"]),
        severity=DriftSeverity(data["severity"]),
        actor_id=data.get("actor_id", ""),
        description=data.get("description", ""),
        observed_value=data.get("observed_value", 0.0),
        baseline_value=data.get("baseline_value", 0.0),
        deviation=data.get("deviation", 0.0),
        timestamp=_str_to_dt(data["timestamp"]),
        metadata=data.get("metadata", {}),
    )


# ── EscalationRule ────────────────────────────────────────────────


def escalation_rule_to_dict(rule: EscalationRule) -> dict[str, Any]:
    """Serialize an EscalationRule to a JSON-safe dict."""
    return {
        "rule_id": rule.rule_id,
        "min_severity": rule.min_severity.value,
        "drift_types": [dt.value for dt in rule.drift_types],
        "action": rule.action.value,
        "threshold_count": rule.threshold_count,
        "cooldown_seconds": rule.cooldown_seconds,
        "enabled": rule.enabled,
        "metadata": rule.metadata,
    }


def escalation_rule_from_dict(data: dict[str, Any]) -> EscalationRule:
    """Deserialize an EscalationRule from a dict."""
    return EscalationRule(
        rule_id=data.get("rule_id", ""),
        min_severity=DriftSeverity(data["min_severity"]),
        drift_types=[DriftType(dt) for dt in data.get("drift_types", [])],
        action=EscalationAction(data["action"]),
        threshold_count=data.get("threshold_count", 1),
        cooldown_seconds=data.get("cooldown_seconds", 60.0),
        enabled=data.get("enabled", True),
        metadata=data.get("metadata", {}),
    )


# ── ConsentNode ───────────────────────────────────────────────────


def consent_node_to_dict(node: ConsentNode) -> dict[str, Any]:
    """Serialize a ConsentNode to a JSON-safe dict."""
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "label": node.label,
        "metadata": node.metadata,
    }


def consent_node_from_dict(data: dict[str, Any]) -> ConsentNode:
    """Deserialize a ConsentNode from a dict."""
    return ConsentNode(
        node_id=data["node_id"],
        node_type=NodeType(data["node_type"]),
        label=data.get("label", ""),
        metadata=data.get("metadata", {}),
    )


# ── ConsentCondition ──────────────────────────────────────────────


def consent_condition_to_dict(cond: ConsentCondition) -> dict[str, Any]:
    """Serialize a ConsentCondition to a JSON-safe dict."""
    return {
        "condition_id": cond.condition_id,
        "expression": cond.expression,
        "description": cond.description,
        "metadata": cond.metadata,
    }


def consent_condition_from_dict(data: dict[str, Any]) -> ConsentCondition:
    """Deserialize a ConsentCondition from a dict."""
    return ConsentCondition(
        condition_id=data.get("condition_id", ""),
        expression=data.get("expression", ""),
        description=data.get("description", ""),
        metadata=data.get("metadata", {}),
    )


# ── ConsentEdge ───────────────────────────────────────────────────


def consent_edge_to_dict(edge: ConsentEdge) -> dict[str, Any]:
    """Serialize a ConsentEdge to a JSON-safe dict."""
    return {
        "edge_id": edge.edge_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "scopes": sorted(edge.scopes),
        "status": edge.status.value,
        "conditions": [consent_condition_to_dict(c) for c in edge.conditions],
        "granted_at": _dt_to_str(edge.granted_at),
        "expires_at": _dt_to_str(edge.expires_at) if edge.expires_at else None,
        "granted_by": edge.granted_by,
        "delegatable": edge.delegatable,
        "max_delegation_depth": edge.max_delegation_depth,
        "delegation_depth": edge.delegation_depth,
        "parent_edge_id": edge.parent_edge_id,
        "metadata": edge.metadata,
    }


def consent_edge_from_dict(data: dict[str, Any]) -> ConsentEdge:
    """Deserialize a ConsentEdge from a dict."""
    conditions = [consent_condition_from_dict(c) for c in data.get("conditions", [])]
    expires_at = _str_to_dt(data["expires_at"]) if data.get("expires_at") else None

    return ConsentEdge(
        edge_id=data["edge_id"],
        source_id=data.get("source_id", ""),
        target_id=data.get("target_id", ""),
        scopes=set(data.get("scopes", [])),
        status=ConsentStatus(data["status"]),
        conditions=conditions,
        granted_at=_str_to_dt(data["granted_at"]),
        expires_at=expires_at,
        granted_by=data.get("granted_by", ""),
        delegatable=data.get("delegatable", False),
        max_delegation_depth=data.get("max_delegation_depth", 0),
        delegation_depth=data.get("delegation_depth", 0),
        parent_edge_id=data.get("parent_edge_id"),
        metadata=data.get("metadata", {}),
    )


# ── ServerRegistration ────────────────────────────────────────────


def server_registration_to_dict(reg: ServerRegistration) -> dict[str, Any]:
    """Serialize a ServerRegistration to a JSON-safe dict."""
    return reg.to_dict()


def server_registration_from_dict(data: dict[str, Any]) -> ServerRegistration:
    """Deserialize a ServerRegistration from a dict."""
    return ServerRegistration(
        server_id=data["server_id"],
        name=data.get("name", ""),
        description=data.get("description", ""),
        endpoint=data.get("endpoint", ""),
        capabilities={ServerCapability(c) for c in data.get("capabilities", [])},
        trust_level=TrustLevel(data["trust_level"]),
        version=data.get("version", ""),
        registered_at=_str_to_dt(data["registered_at"]),
        last_heartbeat=_str_to_dt(data["last_heartbeat"]),
        metadata=data.get("metadata", {}),
        tags=set(data.get("tags", [])),
    )
