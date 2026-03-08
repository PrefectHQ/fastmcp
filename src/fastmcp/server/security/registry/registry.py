"""Trust Registry — persistent store for tool trust profiles.

The TrustRegistry is the central authority on tool trustworthiness.
It stores TrustRecords, computes composite trust scores, and provides
search/query capabilities for consumers making access decisions.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastmcp.server.security.certification.attestation import (
    CertificationLevel,
    ToolAttestation,
)
from fastmcp.server.security.registry.models import (
    CERTIFICATION_BASE_SCORES,
    ReputationEvent,
    ReputationEventType,
    TrustRecord,
    TrustScore,
)

if TYPE_CHECKING:
    from fastmcp.server.security.alerts.bus import SecurityEventBus

logger = logging.getLogger(__name__)


#: Weights for the composite trust score.
DEFAULT_SCORE_WEIGHTS = {
    "certification": 0.50,
    "reputation": 0.35,
    "age": 0.15,
}

#: Age at which the age component reaches 90% of max (in days).
DEFAULT_AGE_HALF_SATURATION_DAYS = 30.0


class TrustRegistry:
    """Persistent registry of tool trust profiles.

    Stores TrustRecords, computes composite trust scores from
    certification level + behavioral reputation + registry tenure,
    and provides query APIs for consumers.

    Example::

        registry = TrustRegistry()

        # Register a tool with its attestation
        record = registry.register(
            tool_name="search-docs",
            tool_version="1.0.0",
            author="acme",
            attestation=attestation,
        )

        # Query trust
        score = registry.get_trust_score("search-docs")
        print(f"Trust: {score.overall:.2f}")

        # Search
        trusted = registry.search(min_trust=0.7, certified_only=True)

    Args:
        score_weights: Weights for score components (must sum to ~1.0).
        age_half_saturation_days: Days until age component reaches ~63%.
        event_bus: Optional event bus for trust change alerts.
    """

    def __init__(
        self,
        *,
        score_weights: dict[str, float] | None = None,
        age_half_saturation_days: float = DEFAULT_AGE_HALF_SATURATION_DAYS,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self._weights = score_weights or dict(DEFAULT_SCORE_WEIGHTS)
        self._age_half_sat = age_half_saturation_days
        self._event_bus = event_bus
        self._records: dict[str, TrustRecord] = {}  # keyed by tool_name

    def register(
        self,
        tool_name: str,
        *,
        tool_version: str = "",
        author: str = "",
        attestation: ToolAttestation | None = None,
        tags: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TrustRecord:
        """Register a tool in the trust registry.

        If a record already exists for this tool_name, it is updated
        (version, author, attestation, tags). Otherwise a new record
        is created.

        Args:
            tool_name: MCP tool name.
            tool_version: Semantic version.
            author: Tool author/publisher.
            attestation: Certification attestation.
            tags: Searchable tags.
            metadata: Additional data.

        Returns:
            The created or updated TrustRecord.
        """
        existing = self._records.get(tool_name)

        if existing is not None:
            existing.tool_version = tool_version or existing.tool_version
            existing.author = author or existing.author
            if attestation is not None:
                existing.attestation = attestation
            if tags:
                existing.tags.update(tags)
            if metadata:
                existing.metadata.update(metadata)
            existing.updated_at = datetime.now(timezone.utc)
            self._recompute_score(existing)
            return existing

        record = TrustRecord(
            tool_name=tool_name,
            tool_version=tool_version,
            author=author,
            attestation=attestation,
            tags=tags or set(),
            metadata=metadata or {},
        )
        self._recompute_score(record)
        self._records[tool_name] = record

        logger.info("Tool registered in trust registry: %s", tool_name)
        return record

    def unregister(self, tool_name: str) -> bool:
        """Remove a tool from the registry.

        Returns True if the tool was found and removed.
        """
        if tool_name not in self._records:
            return False
        del self._records[tool_name]
        return True

    def get(self, tool_name: str) -> TrustRecord | None:
        """Look up a trust record by tool name."""
        return self._records.get(tool_name)

    def get_trust_score(self, tool_name: str) -> TrustScore | None:
        """Get the current trust score for a tool.

        Recomputes the score before returning.
        """
        record = self._records.get(tool_name)
        if record is None:
            return None
        self._recompute_score(record)
        return record.trust_score

    def record_reputation_event(
        self, tool_name: str, event: ReputationEvent
    ) -> bool:
        """Record a reputation event for a tool.

        Updates the trust score after recording.

        Returns True if the tool was found.
        """
        record = self._records.get(tool_name)
        if record is None:
            return False

        record.reputation_events.append(event)
        record.updated_at = datetime.now(timezone.utc)

        old_score = record.trust_score.overall
        self._recompute_score(record)
        new_score = record.trust_score.overall

        # Emit event if significant change
        if self._event_bus is not None and abs(new_score - old_score) >= 0.05:
            self._emit_trust_change(record, old_score, new_score, event)

        return True

    def update_attestation(
        self, tool_name: str, attestation: ToolAttestation
    ) -> bool:
        """Update a tool's attestation.

        Returns True if the tool was found.
        """
        record = self._records.get(tool_name)
        if record is None:
            return False

        record.attestation = attestation
        record.updated_at = datetime.now(timezone.utc)
        self._recompute_score(record)
        return True

    def search(
        self,
        *,
        min_trust: float | None = None,
        max_trust: float | None = None,
        certified_only: bool = False,
        min_certification: CertificationLevel | None = None,
        author: str | None = None,
        tags: set[str] | None = None,
        name_contains: str | None = None,
        max_violations: int | None = None,
        limit: int = 100,
    ) -> list[TrustRecord]:
        """Search the registry with filters.

        All filters are AND-combined.

        Args:
            min_trust: Minimum overall trust score.
            max_trust: Maximum overall trust score.
            certified_only: Only return tools with valid certification.
            min_certification: Minimum certification level.
            author: Filter by author.
            tags: Required tags (any match).
            name_contains: Case-insensitive name search.
            max_violations: Maximum violation count.
            limit: Maximum results.

        Returns:
            Matching records sorted by trust score (descending).
        """
        level_order = list(CertificationLevel)
        results: list[TrustRecord] = []

        for record in self._records.values():
            # Recompute before filtering
            self._recompute_score(record)

            if min_trust is not None and record.trust_score.overall < min_trust:
                continue
            if max_trust is not None and record.trust_score.overall > max_trust:
                continue
            if certified_only and not record.is_certified:
                continue
            if min_certification is not None:
                if level_order.index(record.certification_level) < level_order.index(
                    min_certification
                ):
                    continue
            if author is not None and record.author != author:
                continue
            if tags and not tags.intersection(record.tags):
                continue
            if name_contains and name_contains.lower() not in record.tool_name.lower():
                continue
            if max_violations is not None and record.violation_count > max_violations:
                continue

            results.append(record)

        # Sort by trust score descending
        results.sort(key=lambda r: r.trust_score.overall, reverse=True)
        return results[:limit]

    def get_all(self) -> list[TrustRecord]:
        """Get all records, sorted by trust score descending."""
        for record in self._records.values():
            self._recompute_score(record)
        records = list(self._records.values())
        records.sort(key=lambda r: r.trust_score.overall, reverse=True)
        return records

    @property
    def record_count(self) -> int:
        """Total registered tools."""
        return len(self._records)

    # ── Score computation ────────────────────────────────────────────

    def _recompute_score(self, record: TrustRecord) -> None:
        """Recompute the composite trust score for a record."""
        cert_score = self._certification_score(record)
        rep_score = self._reputation_score(record)
        age_score = self._age_score(record)

        w = self._weights
        overall = (
            w.get("certification", 0.5) * cert_score
            + w.get("reputation", 0.35) * rep_score
            + w.get("age", 0.15) * age_score
        )

        record.trust_score = TrustScore(
            overall=max(0.0, min(1.0, overall)),
            certification_component=cert_score,
            reputation_component=rep_score,
            age_component=age_score,
        )

    def _certification_score(self, record: TrustRecord) -> float:
        """Score from certification level (0.0–1.0)."""
        level = record.certification_level
        return CERTIFICATION_BASE_SCORES.get(level, 0.0)

    def _reputation_score(self, record: TrustRecord) -> float:
        """Score from behavioral reputation (0.0–1.0).

        Starts at 0.5 (neutral). Each event shifts the score based
        on its impact. Bounded to [0.0, 1.0].
        """
        if not record.reputation_events:
            return 0.5  # neutral

        raw = 0.5
        for event in record.reputation_events:
            raw += event.impact * 0.1  # scale impacts to small increments

        return max(0.0, min(1.0, raw))

    def _age_score(self, record: TrustRecord) -> float:
        """Score from registry tenure (0.0–1.0).

        Uses a saturation curve: score = 1 - e^(-t/τ)
        where τ is the half-saturation time.
        """
        now = datetime.now(timezone.utc)
        age_days = (now - record.registered_at).total_seconds() / 86400.0
        if age_days <= 0:
            return 0.0
        tau = self._age_half_sat
        return 1.0 - math.exp(-age_days / tau)

    # ── Event emission ───────────────────────────────────────────────

    def _emit_trust_change(
        self,
        record: TrustRecord,
        old_score: float,
        new_score: float,
        event: ReputationEvent,
    ) -> None:
        """Emit a trust change event."""
        if self._event_bus is None:
            return

        from fastmcp.server.security.alerts.models import (
            AlertSeverity,
            SecurityEvent,
            SecurityEventType,
        )

        severity = AlertSeverity.INFO
        if new_score < old_score:
            severity = AlertSeverity.WARNING
        if new_score < 0.3:
            severity = AlertSeverity.CRITICAL

        self._event_bus.emit(
            SecurityEvent(
                event_type=SecurityEventType.TRUST_CHANGED,
                severity=severity,
                layer="registry",
                message=(
                    f"Trust score changed for '{record.tool_name}': "
                    f"{old_score:.2f} → {new_score:.2f} ({event.event_type.value})"
                ),
                resource_id=record.record_id,
                data={
                    "tool_name": record.tool_name,
                    "old_score": round(old_score, 4),
                    "new_score": round(new_score, 4),
                    "event_type": event.event_type.value,
                },
            )
        )
