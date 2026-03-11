"""Unified event model for the SecureMCP alert system.

All security events across all layers are represented as ``SecurityEvent``
instances with a common structure, enabling consistent filtering, routing,
and handling.

Example::

    event = SecurityEvent(
        event_type=SecurityEventType.DRIFT_DETECTED,
        severity=AlertSeverity.WARNING,
        layer="reflexive",
        actor_id="agent-1",
        resource_id="tool:database",
        message="Behavioral drift detected: call_frequency deviation 3.5σ",
        data={"metric": "call_frequency", "deviation": 3.5},
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SecurityEventType(Enum):
    """Types of security events emitted by SecureMCP components."""

    # Reflexive layer
    DRIFT_DETECTED = "drift_detected"
    ESCALATION_TRIGGERED = "escalation_triggered"

    # Policy layer
    POLICY_DENIED = "policy_denied"
    POLICY_SWAPPED = "policy_swapped"

    # Consent layer
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_DELEGATED = "consent_delegated"

    # Gateway layer
    SERVER_REGISTERED = "server_registered"
    SERVER_UNREGISTERED = "server_unregistered"
    TRUST_CHANGED = "trust_changed"

    # Provenance layer
    PROVENANCE_RECORDED = "provenance_recorded"

    # Contract layer
    CONTRACT_SIGNED = "contract_signed"
    CONTRACT_REVOKED = "contract_revoked"


class AlertSeverity(Enum):
    """Severity levels for security alerts, ordered by urgency."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    def __ge__(self, other: AlertSeverity) -> bool:
        order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.CRITICAL: 2,
        }
        return order[self] >= order[other]

    def __gt__(self, other: AlertSeverity) -> bool:
        order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.CRITICAL: 2,
        }
        return order[self] > order[other]

    def __le__(self, other: AlertSeverity) -> bool:
        order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.CRITICAL: 2,
        }
        return order[self] <= order[other]

    def __lt__(self, other: AlertSeverity) -> bool:
        order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.CRITICAL: 2,
        }
        return order[self] < order[other]


@dataclass(frozen=True)
class SecurityEvent:
    """A security event emitted by a SecureMCP component.

    Attributes:
        event_id: Unique identifier for this event.
        event_type: The type of security event.
        severity: Urgency level (INFO, WARNING, CRITICAL).
        layer: Which security layer emitted this event
            (e.g., "reflexive", "policy", "consent", "gateway", "provenance").
        actor_id: The actor involved (if applicable).
        resource_id: The resource involved (if applicable).
        message: Human-readable description.
        data: Additional event-specific data.
        timestamp: When the event occurred.
    """

    event_type: SecurityEventType
    severity: AlertSeverity
    layer: str
    message: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    actor_id: str | None = None
    resource_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
