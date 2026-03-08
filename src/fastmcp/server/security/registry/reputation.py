"""Reputation Tracker — bridges runtime security events to trust scores.

Listens to security events (policy violations, drift, contract breaches)
and translates them into ReputationEvents that feed into the TrustRegistry.
Also tracks successful executions for positive reputation building.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.registry.models import (
    ReputationEvent,
    ReputationEventType,
)

if TYPE_CHECKING:
    from fastmcp.server.security.registry.registry import TrustRegistry

logger = logging.getLogger(__name__)


#: Default impact values for each reputation event type.
DEFAULT_IMPACTS: dict[ReputationEventType, float] = {
    ReputationEventType.POLICY_VIOLATION: -3.0,
    ReputationEventType.DRIFT_DETECTED: -1.5,
    ReputationEventType.CONTRACT_BREACH: -4.0,
    ReputationEventType.CONSENT_VIOLATION: -5.0,
    ReputationEventType.SUCCESSFUL_EXECUTION: 0.2,
    ReputationEventType.POSITIVE_REVIEW: 2.0,
    ReputationEventType.NEGATIVE_REVIEW: -2.0,
    ReputationEventType.ATTESTATION_RENEWED: 1.0,
    ReputationEventType.ATTESTATION_REVOKED: -8.0,
}


class ReputationTracker:
    """Translates runtime security events into reputation impacts.

    Works as a bridge between the security event system and the
    TrustRegistry. Call report() methods when security-relevant
    things happen; the tracker computes appropriate impact and
    forwards to the registry.

    Example::

        tracker = ReputationTracker(registry=trust_registry)

        # Tool executed successfully
        tracker.report_success("search-docs", actor_id="agent-1")

        # Tool violated a policy
        tracker.report_violation(
            "risky-tool",
            event_type=ReputationEventType.POLICY_VIOLATION,
            description="Attempted unauthorized file write",
            actor_id="agent-2",
        )

    Args:
        registry: The TrustRegistry to record events in.
        impact_overrides: Custom impact values per event type.
    """

    def __init__(
        self,
        *,
        registry: TrustRegistry,
        impact_overrides: dict[ReputationEventType, float] | None = None,
    ) -> None:
        self._registry = registry
        self._impacts = dict(DEFAULT_IMPACTS)
        if impact_overrides:
            self._impacts.update(impact_overrides)

    def report_success(
        self,
        tool_name: str,
        *,
        actor_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Report a successful tool execution.

        Returns True if the tool was found in the registry.
        """
        return self._record(
            tool_name=tool_name,
            event_type=ReputationEventType.SUCCESSFUL_EXECUTION,
            actor_id=actor_id,
            description=f"Successful execution by {actor_id}" if actor_id else "Successful execution",
            metadata=metadata,
        )

    def report_violation(
        self,
        tool_name: str,
        *,
        event_type: ReputationEventType = ReputationEventType.POLICY_VIOLATION,
        actor_id: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Report a security violation by a tool.

        Returns True if the tool was found in the registry.
        """
        return self._record(
            tool_name=tool_name,
            event_type=event_type,
            actor_id=actor_id,
            description=description,
            metadata=metadata,
        )

    def report_review(
        self,
        tool_name: str,
        *,
        positive: bool = True,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Report a human review of a tool.

        Returns True if the tool was found in the registry.
        """
        event_type = (
            ReputationEventType.POSITIVE_REVIEW
            if positive
            else ReputationEventType.NEGATIVE_REVIEW
        )
        return self._record(
            tool_name=tool_name,
            event_type=event_type,
            description=description or ("Positive review" if positive else "Negative review"),
            metadata=metadata,
        )

    def report_attestation_change(
        self,
        tool_name: str,
        *,
        renewed: bool = True,
        description: str = "",
    ) -> bool:
        """Report an attestation lifecycle change.

        Args:
            tool_name: The tool affected.
            renewed: True for renewal, False for revocation.
            description: Additional context.

        Returns True if the tool was found in the registry.
        """
        event_type = (
            ReputationEventType.ATTESTATION_RENEWED
            if renewed
            else ReputationEventType.ATTESTATION_REVOKED
        )
        return self._record(
            tool_name=tool_name,
            event_type=event_type,
            description=description or ("Attestation renewed" if renewed else "Attestation revoked"),
        )

    def get_impacts(self) -> dict[ReputationEventType, float]:
        """Get current impact configuration."""
        return dict(self._impacts)

    def _record(
        self,
        *,
        tool_name: str,
        event_type: ReputationEventType,
        actor_id: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Create and record a reputation event."""
        impact = self._impacts.get(event_type, 0.0)

        event = ReputationEvent(
            event_type=event_type,
            tool_name=tool_name,
            actor_id=actor_id,
            impact=impact,
            description=description,
            metadata=metadata or {},
        )

        return self._registry.record_reputation_event(tool_name, event)
