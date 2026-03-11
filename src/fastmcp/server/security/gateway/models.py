"""Data models for the SecureMCP API Gateway.

Defines request/response schemas for audit queries, marketplace
registration, and health/status endpoints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ── Audit query models ───────────────────────────────────────────────


class AuditQueryType(Enum):
    """Types of audit queries supported by the gateway."""

    PROVENANCE = "provenance"
    DRIFT = "drift"
    CONSENT = "consent"
    CONTRACT = "contract"
    POLICY = "policy"


@dataclass
class AuditQuery:
    """A query to the audit API.

    Attributes:
        query_type: The type of audit data requested.
        actor_id: Filter by actor (optional).
        resource_id: Filter by resource (optional).
        since: Start time for the query window.
        until: End time for the query window.
        limit: Maximum results to return.
        offset: Pagination offset.
        filters: Additional type-specific filters.
    """

    query_type: AuditQueryType = AuditQueryType.PROVENANCE
    actor_id: str | None = None
    resource_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 100
    offset: int = 0
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditResult:
    """Result from an audit API query.

    Attributes:
        query_type: The query type that produced this result.
        total_count: Total matching records (before pagination).
        records: The audit records for this page.
        has_more: Whether more records exist beyond this page.
        metadata: Additional context about the query.
    """

    query_type: AuditQueryType = AuditQueryType.PROVENANCE
    total_count: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)
    has_more: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Marketplace models ───────────────────────────────────────────────


class ServerCapability(Enum):
    """Security capabilities a server can advertise."""

    POLICY_ENGINE = "policy_engine"
    CONTRACT_NEGOTIATION = "contract_negotiation"
    PROVENANCE_LEDGER = "provenance_ledger"
    BEHAVIORAL_MONITORING = "behavioral_monitoring"
    CONSENT_GRAPH = "consent_graph"
    AUDIT_API = "audit_api"


class TrustLevel(Enum):
    """Trust levels for marketplace servers."""

    UNVERIFIED = "unverified"
    SELF_CERTIFIED = "self_certified"
    COMMUNITY_VERIFIED = "community_verified"
    AUDITOR_VERIFIED = "auditor_verified"


@dataclass
class ServerRegistration:
    """A server's registration in the SecureMCP marketplace.

    Attributes:
        server_id: Unique identifier for this server.
        name: Human-readable server name.
        description: What this server provides.
        endpoint: Connection endpoint (URL or transport spec).
        capabilities: Security features this server supports.
        trust_level: Current trust certification level.
        version: Server version string.
        registered_at: When this server was registered.
        last_heartbeat: Last time the server reported healthy.
        metadata: Additional server properties.
        tags: Searchable tags for discovery.
    """

    server_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    endpoint: str = ""
    capabilities: set[ServerCapability] = field(default_factory=set)
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    version: str = ""
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)

    def is_healthy(self, timeout_seconds: float = 300) -> bool:
        """Check if the server has reported recently."""
        elapsed = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        return elapsed < timeout_seconds

    def has_capability(self, cap: ServerCapability) -> bool:
        """Check if the server has a specific capability."""
        return cap in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return {
            "server_id": self.server_id,
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "capabilities": [c.value for c in self.capabilities],
            "trust_level": self.trust_level.value,
            "version": self.version,
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "tags": list(self.tags),
            "metadata": self.metadata,
        }


# ── Health/status models ─────────────────────────────────────────────


class HealthStatus(Enum):
    """Overall health status of the security subsystem."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class SecurityStatus:
    """Status report for all security layers.

    Attributes:
        status: Overall health.
        layers: Per-layer status information.
        uptime_seconds: How long the server has been running.
        total_operations: Total operations processed.
        timestamp: When this status was generated.
    """

    status: HealthStatus = HealthStatus.HEALTHY
    layers: dict[str, dict[str, Any]] = field(default_factory=dict)
    uptime_seconds: float = 0.0
    total_operations: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return {
            "status": self.status.value,
            "layers": self.layers,
            "uptime_seconds": self.uptime_seconds,
            "total_operations": self.total_operations,
            "timestamp": self.timestamp.isoformat(),
        }
