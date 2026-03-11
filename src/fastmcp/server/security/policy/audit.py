"""Policy decision audit log.

Records every policy evaluation with its decision, context, and timing.
Provides a queryable history for compliance, debugging, and analytics.

Example::

    from fastmcp.server.security.policy.audit import PolicyAuditLog

    audit = PolicyAuditLog(max_entries=10_000)

    # Wire to a PolicyEngine
    engine = PolicyEngine(providers=[...], audit_log=audit)

    # Or record manually
    audit.record(
        context=PolicyEvaluationContext(...),
        result=PolicyResult(...),
    )

    # Query the log
    recent = audit.query(limit=10)
    denied = audit.query(decision=PolicyDecision.DENY)
    by_actor = audit.query(actor_id="agent-1")
    by_resource = audit.query(resource_id="admin-*")

    # Export for compliance
    entries = audit.export()
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEntry:
    """A single audit log entry recording a policy decision.

    Attributes:
        actor_id: Who made the request.
        action: What action was attempted.
        resource_id: The target resource.
        decision: The final ALLOW/DENY/DEFER decision.
        reason: Human-readable reason from the policy.
        policy_id: ID of the policy that made the decision.
        constraints: Any constraints attached to an ALLOW.
        metadata: Additional context metadata from the request.
        tags: Tags from the evaluation context.
        timestamp: When the evaluation occurred.
        elapsed_ms: Time taken for the evaluation in milliseconds.
    """

    actor_id: str | None
    action: str
    resource_id: str
    decision: PolicyDecision
    reason: str
    policy_id: str
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Export as a JSON-serializable dict."""
        return {
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_id": self.resource_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "constraints": self.constraints,
            "metadata": self.metadata,
            "tags": sorted(self.tags),
            "timestamp": self.timestamp.isoformat(),
            "elapsed_ms": self.elapsed_ms,
        }


class PolicyAuditLog:
    """In-memory audit log for policy decisions.

    Thread-safe bounded log with query, filtering, and export capabilities.

    Args:
        max_entries: Maximum number of entries to retain (oldest are evicted).
            Defaults to 10,000.

    Example::

        audit = PolicyAuditLog(max_entries=5000)
        audit.record(context, result)
        recent_denials = audit.query(decision=PolicyDecision.DENY, limit=20)
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._total_recorded: int = 0
        self._total_allowed: int = 0
        self._total_denied: int = 0

    @property
    def size(self) -> int:
        """Number of entries currently in the log."""
        return len(self._entries)

    @property
    def max_entries(self) -> int:
        """Maximum capacity of the log."""
        return self._max_entries

    @property
    def total_recorded(self) -> int:
        """Total entries ever recorded (including evicted ones)."""
        return self._total_recorded

    @property
    def total_allowed(self) -> int:
        """Total ALLOW decisions recorded."""
        return self._total_allowed

    @property
    def total_denied(self) -> int:
        """Total DENY decisions recorded."""
        return self._total_denied

    def record(
        self,
        context: PolicyEvaluationContext,
        result: PolicyResult,
        *,
        elapsed_ms: float = 0.0,
    ) -> AuditEntry:
        """Record a policy decision.

        Args:
            context: The evaluation context.
            result: The policy result.
            elapsed_ms: Optional elapsed time for the evaluation.

        Returns:
            The created AuditEntry.
        """
        entry = AuditEntry(
            actor_id=context.actor_id,
            action=context.action,
            resource_id=context.resource_id,
            decision=result.decision,
            reason=result.reason,
            policy_id=result.policy_id,
            constraints=list(result.constraints),
            metadata=dict(context.metadata),
            tags=context.tags,
            timestamp=datetime.now(timezone.utc),
            elapsed_ms=elapsed_ms,
        )

        with self._lock:
            self._entries.append(entry)
            self._total_recorded += 1
            if result.decision == PolicyDecision.ALLOW:
                self._total_allowed += 1
            elif result.decision == PolicyDecision.DENY:
                self._total_denied += 1

        return entry

    def query(
        self,
        *,
        actor_id: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        decision: PolicyDecision | None = None,
        policy_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[AuditEntry]:
        """Query the audit log with optional filters.

        All filters are ANDed together. String filters on ``resource_id``
        support glob patterns (e.g., ``"admin-*"``).

        Args:
            actor_id: Filter by actor ID (exact match).
            resource_id: Filter by resource ID (exact or glob pattern).
            action: Filter by action (exact match).
            decision: Filter by decision type.
            policy_id: Filter by policy ID (exact match).
            since: Only entries after this timestamp.
            until: Only entries before this timestamp.
            limit: Maximum number of results (most recent first).

        Returns:
            Matching entries, ordered most-recent-first.
        """
        with self._lock:
            entries = list(self._entries)

        # Apply filters
        result = []
        for entry in reversed(entries):  # Most recent first
            if actor_id is not None and entry.actor_id != actor_id:
                continue
            if resource_id is not None:
                if not (
                    entry.resource_id == resource_id
                    or fnmatch.fnmatchcase(entry.resource_id, resource_id)
                ):
                    continue
            if action is not None and entry.action != action:
                continue
            if decision is not None and entry.decision != decision:
                continue
            if policy_id is not None and entry.policy_id != policy_id:
                continue
            if since is not None and entry.timestamp < since:
                continue
            if until is not None and entry.timestamp > until:
                continue

            result.append(entry)
            if limit is not None and len(result) >= limit:
                break

        return result

    def clear(self) -> int:
        """Clear all entries from the log.

        Returns:
            Number of entries removed.
        """
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    def export(self) -> list[dict[str, Any]]:
        """Export all entries as JSON-serializable dicts.

        Returns:
            List of entry dicts, ordered oldest-first.
        """
        with self._lock:
            return [entry.to_dict() for entry in self._entries]

    def get_statistics(self) -> dict[str, Any]:
        """Get aggregate statistics about the audit log.

        Returns:
            Dict with counts, rates, and summary info.
        """
        with self._lock:
            entries = list(self._entries)

        # Count by decision
        allow_count = sum(1 for e in entries if e.decision == PolicyDecision.ALLOW)
        deny_count = sum(1 for e in entries if e.decision == PolicyDecision.DENY)
        defer_count = sum(1 for e in entries if e.decision == PolicyDecision.DEFER)

        # Unique actors and resources
        actors = {e.actor_id for e in entries if e.actor_id}
        resources = {e.resource_id for e in entries}

        # Top denied resources
        deny_entries = [e for e in entries if e.decision == PolicyDecision.DENY]
        resource_deny_counts: dict[str, int] = {}
        for e in deny_entries:
            resource_deny_counts[e.resource_id] = (
                resource_deny_counts.get(e.resource_id, 0) + 1
            )
        top_denied = sorted(
            resource_deny_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]

        return {
            "entries_in_log": len(entries),
            "max_entries": self._max_entries,
            "total_recorded": self._total_recorded,
            "total_allowed": self._total_allowed,
            "total_denied": self._total_denied,
            "current_allow": allow_count,
            "current_deny": deny_count,
            "current_defer": defer_count,
            "unique_actors": len(actors),
            "unique_resources": len(resources),
            "top_denied_resources": [
                {"resource_id": r, "count": c} for r, c in top_denied
            ],
            "deny_rate": (deny_count / len(entries) if entries else 0.0),
        }
