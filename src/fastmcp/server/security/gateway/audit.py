"""Audit API for SecureMCP.

Provides a unified query interface across all security layers:
provenance records, drift events, consent decisions, contract
status, and policy evaluations.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp.server.security.consent.graph import ConsentGraph
from fastmcp.server.security.gateway.models import (
    AuditQuery,
    AuditQueryType,
    AuditResult,
    HealthStatus,
    SecurityStatus,
)
from fastmcp.server.security.policy.engine import PolicyEngine
from fastmcp.server.security.provenance.ledger import ProvenanceLedger
from fastmcp.server.security.reflexive.analyzer import BehavioralAnalyzer

logger = logging.getLogger(__name__)


class AuditAPI:
    """Unified audit query API across all security layers.

    Aggregates data from the provenance ledger, behavioral analyzer,
    consent graph, and policy engine into a single query interface.

    Example::

        api = AuditAPI(
            provenance_ledger=ledger,
            behavioral_analyzer=analyzer,
            consent_graph=graph,
            policy_engine=engine,
        )

        result = api.query(AuditQuery(
            query_type=AuditQueryType.PROVENANCE,
            actor_id="agent-1",
            limit=50,
        ))

    Args:
        provenance_ledger: The provenance ledger (Phase 3).
        behavioral_analyzer: The behavioral analyzer (Phase 4).
        consent_graph: The consent graph (Phase 5).
        policy_engine: The policy engine (Phase 1).
    """

    def __init__(
        self,
        *,
        provenance_ledger: ProvenanceLedger | None = None,
        behavioral_analyzer: BehavioralAnalyzer | None = None,
        consent_graph: ConsentGraph | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._provenance = provenance_ledger
        self._analyzer = behavioral_analyzer
        self._consent = consent_graph
        self._policy = policy_engine
        self._start_time = time.monotonic()
        self._query_count = 0

    def query(self, audit_query: AuditQuery) -> AuditResult:
        """Execute an audit query.

        Dispatches to the appropriate handler based on query type.

        Args:
            audit_query: The query to execute.

        Returns:
            AuditResult with matching records.
        """
        self._query_count += 1

        handlers = {
            AuditQueryType.PROVENANCE: self._query_provenance,
            AuditQueryType.DRIFT: self._query_drift,
            AuditQueryType.CONSENT: self._query_consent,
            AuditQueryType.CONTRACT: self._query_contract,
            AuditQueryType.POLICY: self._query_policy,
        }

        handler = handlers.get(audit_query.query_type)
        if handler is None:
            return AuditResult(
                query_type=audit_query.query_type,
                metadata={"error": f"Unknown query type: {audit_query.query_type}"},
            )

        return handler(audit_query)

    def get_status(self) -> SecurityStatus:
        """Get the current status of all security layers.

        Returns:
            SecurityStatus with per-layer health information.
        """
        layers: dict[str, dict[str, Any]] = {}

        if self._provenance is not None:
            layers["provenance"] = {
                "enabled": True,
                "record_count": self._provenance.record_count,
                "chain_valid": self._provenance.verify_chain(),
                "tree_valid": self._provenance.verify_tree(),
                "root_hash": self._provenance.root_hash,
            }

        if self._analyzer is not None:
            layers["reflexive"] = {
                "enabled": True,
                "total_drift_events": self._analyzer.total_drift_count,
            }

        if self._consent is not None:
            layers["consent"] = {
                "enabled": True,
                "node_count": self._consent.node_count,
                "edge_count": self._consent.edge_count,
            }

        if self._policy is not None:
            layers["policy"] = {
                "enabled": True,
                "provider_count": len(self._policy.providers),
                "evaluation_count": self._policy.evaluation_count,
                "deny_count": self._policy.deny_count,
            }

        # Determine overall status
        status = HealthStatus.HEALTHY
        if self._provenance is not None and not self._provenance.verify_chain():
            status = HealthStatus.DEGRADED

        return SecurityStatus(
            status=status,
            layers=layers,
            uptime_seconds=time.monotonic() - self._start_time,
            total_operations=self._query_count,
        )

    @property
    def query_count(self) -> int:
        """Total number of queries processed."""
        return self._query_count

    # ── Query handlers ───────────────────────────────────────────────

    def _query_provenance(self, q: AuditQuery) -> AuditResult:
        """Query provenance records."""
        if self._provenance is None:
            return AuditResult(
                query_type=q.query_type,
                metadata={"error": "Provenance ledger not configured"},
            )

        # Get action filter from filters dict
        action_filter = q.filters.get("action")

        records = self._provenance.get_records(
            action=action_filter,
            actor_id=q.actor_id,
            resource_id=q.resource_id,
            since=q.since,
            until=q.until,
            limit=q.limit + 1,  # Fetch one extra to check has_more
        )

        has_more = len(records) > q.limit
        if has_more:
            records = records[: q.limit]

        return AuditResult(
            query_type=q.query_type,
            total_count=self._provenance.record_count,
            records=[r.to_dict() for r in records],
            has_more=has_more,
            metadata={"ledger_id": self._provenance.ledger_id},
        )

    def _query_drift(self, q: AuditQuery) -> AuditResult:
        """Query drift events from the behavioral analyzer."""
        if self._analyzer is None:
            return AuditResult(
                query_type=q.query_type,
                metadata={"error": "Behavioral analyzer not configured"},
            )

        severity_filter = q.filters.get("severity")
        events = self._analyzer.get_drift_history(
            actor_id=q.actor_id,
            severity=severity_filter,
            limit=q.limit,
        )

        records = [
            {
                "event_id": e.event_id,
                "drift_type": e.drift_type.value,
                "severity": e.severity.value,
                "actor_id": e.actor_id,
                "description": e.description,
                "observed_value": e.observed_value,
                "baseline_value": e.baseline_value,
                "deviation": e.deviation,
                "timestamp": e.timestamp.isoformat(),
                "metadata": e.metadata,
            }
            for e in events
        ]

        return AuditResult(
            query_type=q.query_type,
            total_count=self._analyzer.total_drift_count,
            records=records,
            has_more=len(records) >= q.limit,
        )

    def _query_consent(self, q: AuditQuery) -> AuditResult:
        """Query consent audit log."""
        if self._consent is None:
            return AuditResult(
                query_type=q.query_type,
                metadata={"error": "Consent graph not configured"},
            )

        log = self._consent.get_audit_log(limit=q.limit)

        # Filter by actor if specified
        if q.actor_id:
            log = [
                entry
                for entry in log
                if entry.get("source_id") == q.actor_id
                or entry.get("target_id") == q.actor_id
                or entry.get("new_target_id") == q.actor_id
            ]

        return AuditResult(
            query_type=q.query_type,
            total_count=len(log),
            records=log[: q.limit],
            has_more=len(log) > q.limit,
            metadata={
                "graph_id": self._consent.graph_id,
                "node_count": self._consent.node_count,
                "edge_count": self._consent.edge_count,
            },
        )

    def _query_contract(self, q: AuditQuery) -> AuditResult:
        """Query contract information.

        This returns summary info from the policy layer since
        the ContextBroker is managed at middleware level.
        """
        # Contract queries are informational — we report what we know
        return AuditResult(
            query_type=q.query_type,
            metadata={
                "info": "Contract audit data is available through the "
                "exchange log and context broker APIs directly.",
            },
        )

    def _query_policy(self, q: AuditQuery) -> AuditResult:
        """Query policy evaluation summary."""
        if self._policy is None:
            return AuditResult(
                query_type=q.query_type,
                metadata={"error": "Policy engine not configured"},
            )

        records = [
            {
                "provider_count": len(self._policy.providers),
                "evaluation_count": self._policy.evaluation_count,
                "deny_count": self._policy.deny_count,
                "providers": [
                    getattr(p, "policy_id", type(p).__name__)
                    for p in self._policy.providers
                ],
            }
        ]

        return AuditResult(
            query_type=q.query_type,
            total_count=1,
            records=records,
            metadata={"fail_closed": self._policy.fail_closed},
        )
