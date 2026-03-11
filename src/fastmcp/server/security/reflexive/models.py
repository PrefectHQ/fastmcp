"""Data models for the Reflexive Core.

Defines behavioral baselines, drift events, and escalation levels
used to detect and respond to anomalous agent behavior.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DriftSeverity(Enum):
    """Severity levels for detected behavioral drift."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftType(Enum):
    """Categories of behavioral drift."""

    FREQUENCY_SPIKE = "frequency_spike"
    NEW_RESOURCE_ACCESS = "new_resource_access"
    UNUSUAL_PATTERN = "unusual_pattern"
    POLICY_VIOLATION_RATE = "policy_violation_rate"
    ERROR_RATE = "error_rate"
    LATENCY_ANOMALY = "latency_anomaly"
    SCOPE_EXPANSION = "scope_expansion"
    CUSTOM = "custom"


class EscalationAction(Enum):
    """Actions taken in response to detected drift."""

    LOG = "log"
    ALERT = "alert"
    THROTTLE = "throttle"
    REQUIRE_CONFIRMATION = "require_confirmation"
    SUSPEND_AGENT = "suspend_agent"
    REVOKE_CONTRACT = "revoke_contract"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class DriftEvent:
    """A detected behavioral drift event.

    Attributes:
        event_id: Unique identifier.
        drift_type: Category of drift detected.
        severity: How severe the drift is.
        actor_id: The agent exhibiting drift.
        description: Human-readable description.
        observed_value: The anomalous value that triggered detection.
        baseline_value: The expected baseline value.
        deviation: How far the observed value deviates (e.g., sigma count).
        timestamp: When drift was detected.
        metadata: Additional context.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    drift_type: DriftType = DriftType.CUSTOM
    severity: DriftSeverity = DriftSeverity.INFO
    actor_id: str = ""
    description: str = ""
    observed_value: float = 0.0
    baseline_value: float = 0.0
    deviation: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BehavioralBaseline:
    """A statistical baseline for a specific behavioral metric.

    Tracks rolling statistics (mean, std dev, min, max) for a metric
    to enable drift detection via sigma-based thresholds.

    Attributes:
        metric_name: Name of the metric being tracked.
        actor_id: The agent this baseline applies to.
        sample_count: Number of observations.
        mean: Running mean of the metric.
        variance: Running variance (for std dev computation).
        min_value: Minimum observed value.
        max_value: Maximum observed value.
        last_updated: When the baseline was last updated.
    """

    metric_name: str = ""
    actor_id: str = ""
    sample_count: int = 0
    mean: float = 0.0
    variance: float = 0.0
    min_value: float = float("inf")
    max_value: float = float("-inf")
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def std_dev(self) -> float:
        """Standard deviation of the metric."""
        if self.sample_count < 2:
            return 0.0
        return (self.variance / (self.sample_count - 1)) ** 0.5

    def update(self, value: float) -> None:
        """Update the baseline with a new observation using Welford's algorithm."""
        self.sample_count += 1
        delta = value - self.mean
        self.mean += delta / self.sample_count
        delta2 = value - self.mean
        self.variance += delta * delta2

        if value < self.min_value:
            self.min_value = value
        if value > self.max_value:
            self.max_value = value
        self.last_updated = datetime.now(timezone.utc)

    def compute_deviation(self, value: float) -> float:
        """Compute how many standard deviations a value is from the mean.

        Returns 0.0 if insufficient data for a meaningful calculation.
        """
        sd = self.std_dev
        if sd == 0.0 or self.sample_count < 5:
            return 0.0
        return abs(value - self.mean) / sd


@dataclass
class EscalationRule:
    """A rule mapping drift severity to escalation actions.

    Attributes:
        rule_id: Unique identifier.
        min_severity: Minimum drift severity that triggers this rule.
        drift_types: Which drift types this rule applies to (empty = all).
        action: The escalation action to take.
        threshold_count: Number of drift events needed before triggering.
        cooldown_seconds: Minimum seconds between escalation triggers.
        enabled: Whether this rule is active.
        metadata: Additional rule configuration.
    """

    rule_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    min_severity: DriftSeverity = DriftSeverity.MEDIUM
    drift_types: list[DriftType] = field(default_factory=list)
    action: EscalationAction = EscalationAction.ALERT
    threshold_count: int = 1
    cooldown_seconds: float = 60.0
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches(self, event: DriftEvent) -> bool:
        """Check if a drift event matches this rule."""
        if not self.enabled:
            return False

        severity_order = list(DriftSeverity)
        if severity_order.index(event.severity) < severity_order.index(
            self.min_severity
        ):
            return False

        if self.drift_types and event.drift_type not in self.drift_types:
            return False

        return True
