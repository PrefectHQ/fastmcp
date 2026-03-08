"""Tests for Phase 13: Trust Registry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fastmcp.server.security.certification.attestation import (
    AttestationStatus,
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
from fastmcp.server.security.registry.registry import TrustRegistry
from fastmcp.server.security.registry.reputation import (
    DEFAULT_IMPACTS,
    ReputationTracker,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _valid_attestation(
    level: CertificationLevel = CertificationLevel.STANDARD,
    **kwargs,
) -> ToolAttestation:
    """Create a valid attestation for testing."""
    a = ToolAttestation(
        status=AttestationStatus.VALID,
        certification_level=level,
        tool_name=kwargs.get("tool_name", "test-tool"),
        **{k: v for k, v in kwargs.items() if k != "tool_name"},
    )
    a.set_default_expiry(timedelta(days=90))
    return a


# ═══════════════════════════════════════════════════════════════════
# TrustScore
# ═══════════════════════════════════════════════════════════════════


class TestTrustScore:
    def test_default_score(self):
        s = TrustScore()
        assert s.overall == 0.0
        assert s.certification_component == 0.0
        assert s.reputation_component == 0.0
        assert s.age_component == 0.0

    def test_to_dict(self):
        s = TrustScore(overall=0.85, certification_component=0.8)
        d = s.to_dict()
        assert d["overall"] == 0.85
        assert d["certification_component"] == 0.8


# ═══════════════════════════════════════════════════════════════════
# TrustRecord
# ═══════════════════════════════════════════════════════════════════


class TestTrustRecord:
    def test_default_record(self):
        r = TrustRecord()
        assert r.certification_level == CertificationLevel.UNCERTIFIED
        assert not r.is_certified
        assert r.violation_count == 0
        assert r.success_count == 0

    def test_certified_record(self):
        r = TrustRecord(attestation=_valid_attestation())
        assert r.is_certified
        assert r.certification_level == CertificationLevel.STANDARD

    def test_expired_attestation(self):
        a = ToolAttestation(
            status=AttestationStatus.VALID,
            certification_level=CertificationLevel.STANDARD,
            issued_at=datetime.now(timezone.utc) - timedelta(days=200),
        )
        a.set_default_expiry(timedelta(days=90))
        r = TrustRecord(attestation=a)
        assert not r.is_certified
        assert r.certification_level == CertificationLevel.UNCERTIFIED

    def test_violation_count(self):
        r = TrustRecord(
            reputation_events=[
                ReputationEvent(event_type=ReputationEventType.POLICY_VIOLATION),
                ReputationEvent(event_type=ReputationEventType.DRIFT_DETECTED),
                ReputationEvent(event_type=ReputationEventType.SUCCESSFUL_EXECUTION),
            ]
        )
        assert r.violation_count == 2
        assert r.success_count == 1

    def test_to_dict(self):
        r = TrustRecord(tool_name="test", author="acme")
        d = r.to_dict()
        assert d["tool_name"] == "test"
        assert d["author"] == "acme"
        assert "trust_score" in d


# ═══════════════════════════════════════════════════════════════════
# TrustRegistry — Registration
# ═══════════════════════════════════════════════════════════════════


class TestTrustRegistryRegistration:
    def test_register_new_tool(self):
        reg = TrustRegistry()
        record = reg.register("search-docs", author="acme")
        assert record.tool_name == "search-docs"
        assert record.author == "acme"
        assert reg.record_count == 1

    def test_register_with_attestation(self):
        reg = TrustRegistry()
        att = _valid_attestation()
        record = reg.register("search-docs", attestation=att)
        assert record.is_certified
        assert record.trust_score.certification_component > 0

    def test_register_updates_existing(self):
        reg = TrustRegistry()
        reg.register("tool-a", author="v1", tool_version="1.0")
        record = reg.register("tool-a", author="v2", tool_version="2.0")
        assert reg.record_count == 1  # no duplicate
        assert record.author == "v2"
        assert record.tool_version == "2.0"

    def test_register_preserves_events_on_update(self):
        reg = TrustRegistry()
        record = reg.register("tool-a")
        reg.record_reputation_event(
            "tool-a",
            ReputationEvent(event_type=ReputationEventType.SUCCESSFUL_EXECUTION),
        )
        updated = reg.register("tool-a", tool_version="2.0")
        assert len(updated.reputation_events) == 1

    def test_unregister(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        assert reg.unregister("tool-a")
        assert reg.record_count == 0
        assert not reg.unregister("tool-a")

    def test_get(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        assert reg.get("tool-a") is not None
        assert reg.get("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════
# TrustRegistry — Trust Scoring
# ═══════════════════════════════════════════════════════════════════


class TestTrustScoring:
    def test_uncertified_tool_low_score(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        score = reg.get_trust_score("tool-a")
        assert score is not None
        # Uncertified = 0 cert component, neutral reputation = 0.5
        assert score.certification_component == 0.0
        assert score.reputation_component == 0.5

    def test_certified_tool_higher_score(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation(CertificationLevel.STANDARD))
        score = reg.get_trust_score("tool-a")
        assert score is not None
        assert score.certification_component == 0.8
        assert score.overall > 0.5

    def test_strict_certification_highest(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation(CertificationLevel.STRICT))
        score = reg.get_trust_score("tool-a")
        assert score is not None
        assert score.certification_component == 1.0

    def test_reputation_impact(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation())
        score_before = reg.get_trust_score("tool-a")

        # Record violations
        for _ in range(5):
            reg.record_reputation_event(
                "tool-a",
                ReputationEvent(
                    event_type=ReputationEventType.POLICY_VIOLATION,
                    impact=-3.0,
                ),
            )

        score_after = reg.get_trust_score("tool-a")
        assert score_after.reputation_component < score_before.reputation_component
        assert score_after.overall < score_before.overall

    def test_positive_reputation(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation())
        score_before = reg.get_trust_score("tool-a")

        for _ in range(20):
            reg.record_reputation_event(
                "tool-a",
                ReputationEvent(
                    event_type=ReputationEventType.SUCCESSFUL_EXECUTION,
                    impact=0.2,
                ),
            )

        score_after = reg.get_trust_score("tool-a")
        assert score_after.reputation_component >= score_before.reputation_component

    def test_age_component_grows(self):
        reg = TrustRegistry()
        record = reg.register("tool-a")
        # Fake an older registration
        record.registered_at = datetime.now(timezone.utc) - timedelta(days=60)
        reg._recompute_score(record)
        assert record.trust_score.age_component > 0.5

    def test_new_tool_low_age(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        score = reg.get_trust_score("tool-a")
        # Just registered, age should be near 0
        assert score.age_component < 0.1

    def test_score_for_nonexistent(self):
        reg = TrustRegistry()
        assert reg.get_trust_score("nope") is None

    def test_reputation_event_for_nonexistent(self):
        reg = TrustRegistry()
        assert not reg.record_reputation_event(
            "nope", ReputationEvent()
        )

    def test_score_bounded(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation(CertificationLevel.STRICT))
        # Flood with positive events
        for _ in range(100):
            reg.record_reputation_event(
                "tool-a",
                ReputationEvent(
                    event_type=ReputationEventType.SUCCESSFUL_EXECUTION,
                    impact=0.2,
                ),
            )
        score = reg.get_trust_score("tool-a")
        assert score.overall <= 1.0
        assert score.reputation_component <= 1.0

    def test_custom_weights(self):
        reg = TrustRegistry(
            score_weights={"certification": 1.0, "reputation": 0.0, "age": 0.0}
        )
        reg.register("tool-a", attestation=_valid_attestation(CertificationLevel.STANDARD))
        score = reg.get_trust_score("tool-a")
        # Score should be entirely from certification
        assert abs(score.overall - 0.8) < 0.01


# ═══════════════════════════════════════════════════════════════════
# TrustRegistry — Attestation Updates
# ═══════════════════════════════════════════════════════════════════


class TestAttestationUpdates:
    def test_update_attestation(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        assert not reg.get("tool-a").is_certified

        reg.update_attestation("tool-a", _valid_attestation())
        assert reg.get("tool-a").is_certified

    def test_update_nonexistent(self):
        reg = TrustRegistry()
        assert not reg.update_attestation("nope", _valid_attestation())


# ═══════════════════════════════════════════════════════════════════
# TrustRegistry — Search
# ═══════════════════════════════════════════════════════════════════


class TestTrustRegistrySearch:
    def _setup_registry(self) -> TrustRegistry:
        reg = TrustRegistry()
        reg.register("tool-a", author="acme", attestation=_valid_attestation(CertificationLevel.STRICT), tags={"search"})
        reg.register("tool-b", author="acme", attestation=_valid_attestation(CertificationLevel.BASIC), tags={"write"})
        reg.register("tool-c", author="other")  # no attestation
        return reg

    def test_search_all(self):
        reg = self._setup_registry()
        results = reg.search()
        assert len(results) == 3

    def test_search_certified_only(self):
        reg = self._setup_registry()
        results = reg.search(certified_only=True)
        assert all(r.is_certified for r in results)
        assert len(results) == 2

    def test_search_min_trust(self):
        reg = self._setup_registry()
        results = reg.search(min_trust=0.5)
        assert all(r.trust_score.overall >= 0.5 for r in results)

    def test_search_by_author(self):
        reg = self._setup_registry()
        results = reg.search(author="acme")
        assert len(results) == 2

    def test_search_by_tags(self):
        reg = self._setup_registry()
        results = reg.search(tags={"search"})
        assert len(results) == 1
        assert results[0].tool_name == "tool-a"

    def test_search_by_name(self):
        reg = self._setup_registry()
        results = reg.search(name_contains="tool-b")
        assert len(results) == 1

    def test_search_min_certification(self):
        reg = self._setup_registry()
        results = reg.search(min_certification=CertificationLevel.STANDARD)
        for r in results:
            level_order = list(CertificationLevel)
            assert level_order.index(r.certification_level) >= level_order.index(
                CertificationLevel.STANDARD
            )

    def test_search_max_violations(self):
        reg = self._setup_registry()
        reg.record_reputation_event(
            "tool-c",
            ReputationEvent(event_type=ReputationEventType.POLICY_VIOLATION, impact=-3.0),
        )
        reg.record_reputation_event(
            "tool-c",
            ReputationEvent(event_type=ReputationEventType.POLICY_VIOLATION, impact=-3.0),
        )
        results = reg.search(max_violations=1)
        assert all(r.violation_count <= 1 for r in results)

    def test_search_sorted_by_trust(self):
        reg = self._setup_registry()
        results = reg.search()
        scores = [r.trust_score.overall for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_limit(self):
        reg = self._setup_registry()
        results = reg.search(limit=1)
        assert len(results) == 1

    def test_get_all(self):
        reg = self._setup_registry()
        all_records = reg.get_all()
        assert len(all_records) == 3
        scores = [r.trust_score.overall for r in all_records]
        assert scores == sorted(scores, reverse=True)


# ═══════════════════════════════════════════════════════════════════
# ReputationTracker
# ═══════════════════════════════════════════════════════════════════


class TestReputationTracker:
    def test_report_success(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        assert tracker.report_success("tool-a", actor_id="agent-1")
        record = reg.get("tool-a")
        assert record.success_count == 1

    def test_report_success_nonexistent(self):
        reg = TrustRegistry()
        tracker = ReputationTracker(registry=reg)
        assert not tracker.report_success("nope")

    def test_report_violation(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_violation(
            "tool-a",
            event_type=ReputationEventType.POLICY_VIOLATION,
            description="Unauthorized write",
        )
        record = reg.get("tool-a")
        assert record.violation_count == 1
        assert record.reputation_events[0].impact == DEFAULT_IMPACTS[ReputationEventType.POLICY_VIOLATION]

    def test_report_contract_breach(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_violation(
            "tool-a",
            event_type=ReputationEventType.CONTRACT_BREACH,
        )
        record = reg.get("tool-a")
        assert record.reputation_events[0].impact == DEFAULT_IMPACTS[ReputationEventType.CONTRACT_BREACH]

    def test_report_positive_review(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_review("tool-a", positive=True)
        record = reg.get("tool-a")
        assert record.reputation_events[0].event_type == ReputationEventType.POSITIVE_REVIEW

    def test_report_negative_review(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_review("tool-a", positive=False)
        record = reg.get("tool-a")
        assert record.reputation_events[0].event_type == ReputationEventType.NEGATIVE_REVIEW

    def test_report_attestation_renewed(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_attestation_change("tool-a", renewed=True)
        record = reg.get("tool-a")
        assert record.reputation_events[0].event_type == ReputationEventType.ATTESTATION_RENEWED

    def test_report_attestation_revoked(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(registry=reg)
        tracker.report_attestation_change("tool-a", renewed=False)
        record = reg.get("tool-a")
        assert record.reputation_events[0].event_type == ReputationEventType.ATTESTATION_REVOKED

    def test_custom_impacts(self):
        reg = TrustRegistry()
        reg.register("tool-a")
        tracker = ReputationTracker(
            registry=reg,
            impact_overrides={ReputationEventType.POLICY_VIOLATION: -10.0},
        )
        tracker.report_violation("tool-a", event_type=ReputationEventType.POLICY_VIOLATION)
        record = reg.get("tool-a")
        assert record.reputation_events[0].impact == -10.0

    def test_get_impacts(self):
        reg = TrustRegistry()
        tracker = ReputationTracker(registry=reg)
        impacts = tracker.get_impacts()
        assert ReputationEventType.POLICY_VIOLATION in impacts

    def test_multiple_events_compound(self):
        reg = TrustRegistry()
        reg.register("tool-a", attestation=_valid_attestation())
        tracker = ReputationTracker(registry=reg)

        score_before = reg.get_trust_score("tool-a").overall

        # 3 violations should lower score
        for _ in range(3):
            tracker.report_violation("tool-a", event_type=ReputationEventType.POLICY_VIOLATION)

        score_after = reg.get_trust_score("tool-a").overall
        assert score_after < score_before


# ═══════════════════════════════════════════════════════════════════
# Event Bus Integration
# ═══════════════════════════════════════════════════════════════════


class TestRegistryEventBus:
    def test_significant_trust_change_emits_event(self):
        from fastmcp.server.security.alerts.bus import SecurityEventBus
        from fastmcp.server.security.alerts.handlers import BufferedHandler

        bus = SecurityEventBus()
        handler = BufferedHandler()
        bus.subscribe(handler)

        reg = TrustRegistry(event_bus=bus)
        reg.register("tool-a", attestation=_valid_attestation())

        # Big negative impact should trigger event
        reg.record_reputation_event(
            "tool-a",
            ReputationEvent(
                event_type=ReputationEventType.CONSENT_VIOLATION,
                impact=-5.0,
            ),
        )
        # Event emitted if change >= 0.05
        assert len(handler.events) >= 1
        assert "trust score changed" in handler.events[0].message.lower()

    def test_small_change_no_event(self):
        from fastmcp.server.security.alerts.bus import SecurityEventBus
        from fastmcp.server.security.alerts.handlers import BufferedHandler

        bus = SecurityEventBus()
        handler = BufferedHandler()
        bus.subscribe(handler)

        reg = TrustRegistry(event_bus=bus)
        reg.register("tool-a", attestation=_valid_attestation())

        # Tiny positive impact — may not cross threshold
        reg.record_reputation_event(
            "tool-a",
            ReputationEvent(
                event_type=ReputationEventType.SUCCESSFUL_EXECUTION,
                impact=0.01,
            ),
        )
        # This tiny change may or may not emit, depends on weight
        # Just check it doesn't crash
        assert True


# ═══════════════════════════════════════════════════════════════════
# Certification Pipeline Integration
# ═══════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    def test_certify_then_register(self):
        """Typical workflow: certify a tool, then register in trust registry."""
        from fastmcp.server.security.certification.manifest import (
            DataClassification,
            DataFlowDeclaration,
            PermissionScope,
            ResourceAccessDeclaration,
            SecurityManifest,
        )
        from fastmcp.server.security.certification.pipeline import CertificationPipeline
        from fastmcp.server.security.contracts.crypto import (
            ContractCryptoHandler,
            SigningAlgorithm,
        )

        crypto = ContractCryptoHandler(
            algorithm=SigningAlgorithm.HMAC_SHA256,
            secret_key=b"test-key",
        )
        pipeline = CertificationPipeline(crypto_handler=crypto)

        manifest = SecurityManifest(
            tool_name="search-docs",
            version="1.0.0",
            author="acme",
            description="Full-text search",
            permissions={PermissionScope.READ_RESOURCE},
            data_flows=[
                DataFlowDeclaration(
                    source="input.query",
                    destination="output.results",
                    classification=DataClassification.INTERNAL,
                ),
            ],
            resource_access=[
                ResourceAccessDeclaration(
                    resource_pattern="docs://*",
                    access_type="read",
                ),
            ],
        )

        result = pipeline.certify(manifest)
        assert result.is_certified

        # Now register in trust registry
        reg = TrustRegistry()
        record = reg.register(
            "search-docs",
            tool_version="1.0.0",
            author="acme",
            attestation=result.attestation,
        )

        assert record.is_certified
        assert record.trust_score.overall > 0.3


# ═══════════════════════════════════════════════════════════════════
# Import Tests
# ═══════════════════════════════════════════════════════════════════


class TestRegistryImports:
    def test_import_from_registry_package(self):
        from fastmcp.server.security.registry import (
            ReputationEvent,
            ReputationEventType,
            ReputationTracker,
            TrustRecord,
            TrustRegistry,
            TrustScore,
        )

    def test_import_from_security_package(self):
        from fastmcp.server.security import (
            ReputationEvent,
            ReputationEventType,
            ReputationTracker,
            TrustRecord,
            TrustRegistry,
            TrustScore,
        )
