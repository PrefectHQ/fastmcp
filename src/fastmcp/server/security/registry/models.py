"""Data models for the Trust Registry.

TrustRecord is the central entity: it binds a tool's identity to its
certification attestation, behavioral reputation, and a composite
trust score that consumers use to make access decisions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
    ToolAttestation,
)


class ReputationEventType(Enum):
    """Types of events that affect a tool's reputation."""

    POLICY_VIOLATION = "policy_violation"
    DRIFT_DETECTED = "drift_detected"
    CONTRACT_BREACH = "contract_breach"
    CONSENT_VIOLATION = "consent_violation"
    SUCCESSFUL_EXECUTION = "successful_execution"
    POSITIVE_REVIEW = "positive_review"
    NEGATIVE_REVIEW = "negative_review"
    ATTESTATION_RENEWED = "attestation_renewed"
    ATTESTATION_REVOKED = "attestation_revoked"


@dataclass
class ReputationEvent:
    """A single event that modifies a tool's reputation.

    Attributes:
        event_id: Unique identifier.
        event_type: What kind of reputation event.
        tool_name: The tool affected.
        actor_id: Who triggered the event (if applicable).
        impact: Score impact (-1.0 to +1.0). Negative = bad, positive = good.
        description: Human-readable description.
        timestamp: When the event occurred.
        metadata: Additional context.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    event_type: ReputationEventType = ReputationEventType.SUCCESSFUL_EXECUTION
    tool_name: str = ""
    actor_id: str = ""
    impact: float = 0.0
    description: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrustScore:
    """Composite trust score for a tool.

    Combines certification level, behavioral reputation, and time-based
    factors into a single normalized score.

    Attributes:
        overall: Composite score from 0.0 (untrusted) to 1.0 (fully trusted).
        certification_component: Score from certification level (0.0–1.0).
        reputation_component: Score from behavioral history (0.0–1.0).
        age_component: Score from registry tenure (0.0–1.0).
        computed_at: When this score was last computed.
    """

    overall: float = 0.0
    certification_component: float = 0.0
    reputation_component: float = 0.0
    age_component: float = 0.0
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "overall": round(self.overall, 4),
            "certification_component": round(self.certification_component, 4),
            "reputation_component": round(self.reputation_component, 4),
            "age_component": round(self.age_component, 4),
            "computed_at": self.computed_at.isoformat(),
        }


#: Base scores for each certification level.
CERTIFICATION_BASE_SCORES: dict[CertificationLevel, float] = {
    CertificationLevel.STRICT: 1.0,
    CertificationLevel.STANDARD: 0.8,
    CertificationLevel.BASIC: 0.6,
    CertificationLevel.SELF_ATTESTED: 0.3,
    CertificationLevel.UNCERTIFIED: 0.0,
}


@dataclass
class TrustRecord:
    """A tool's complete trust profile in the registry.

    Attributes:
        record_id: Unique registry record identifier.
        tool_name: MCP tool name.
        tool_version: Current version.
        author: Tool author/publisher.
        attestation: The current certification attestation (if any).
        reputation_events: History of reputation-affecting events.
        trust_score: Latest computed trust score.
        registered_at: When the tool was first registered.
        updated_at: When the record was last modified.
        tags: Searchable tags.
        metadata: Additional record data.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    tool_version: str = ""
    author: str = ""
    attestation: ToolAttestation | None = None
    reputation_events: list[ReputationEvent] = field(default_factory=list)
    trust_score: TrustScore = field(default_factory=TrustScore)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def certification_level(self) -> CertificationLevel:
        """Current certification level from attestation."""
        if self.attestation is not None and self.attestation.is_valid():
            return self.attestation.certification_level
        return CertificationLevel.UNCERTIFIED

    @property
    def is_certified(self) -> bool:
        """Whether the tool has a valid certification."""
        return self.attestation is not None and self.attestation.is_valid()

    @property
    def violation_count(self) -> int:
        """Number of negative reputation events."""
        negative_types = {
            ReputationEventType.POLICY_VIOLATION,
            ReputationEventType.DRIFT_DETECTED,
            ReputationEventType.CONTRACT_BREACH,
            ReputationEventType.CONSENT_VIOLATION,
            ReputationEventType.NEGATIVE_REVIEW,
            ReputationEventType.ATTESTATION_REVOKED,
        }
        return sum(1 for e in self.reputation_events if e.event_type in negative_types)

    @property
    def success_count(self) -> int:
        """Number of successful execution events."""
        return sum(
            1
            for e in self.reputation_events
            if e.event_type == ReputationEventType.SUCCESSFUL_EXECUTION
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "record_id": self.record_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "author": self.author,
            "certification_level": self.certification_level.value,
            "is_certified": self.is_certified,
            "trust_score": self.trust_score.to_dict(),
            "violation_count": self.violation_count,
            "success_count": self.success_count,
            "registered_at": self.registered_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "tags": sorted(self.tags),
            "metadata": self.metadata,
        }
