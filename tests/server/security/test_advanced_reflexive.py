"""Tests for Phase 11: Advanced Anomaly Detection.

Covers SlidingWindowDetector, PatternDetector, ActorProfile/Manager,
and integration with BehavioralAnalyzer.
"""

from __future__ import annotations

import time

import pytest

from fastmcp.server.security.reflexive.analyzer import BehavioralAnalyzer
from fastmcp.server.security.reflexive.detectors import (
    AnomalyDetector,
    OperationPattern,
    PatternDetector,
    SlidingWindowDetector,
    WindowConfig,
)
from fastmcp.server.security.reflexive.models import (
    DriftEvent,
    DriftSeverity,
    DriftType,
)
from fastmcp.server.security.reflexive.profiles import (
    ActorProfile,
    ActorProfileManager,
)

# ═══════════════════════════════════════════════════════════════════════
# AnomalyDetector protocol
# ═══════════════════════════════════════════════════════════════════════


class TestAnomalyDetectorProtocol:
    def test_sliding_window_is_anomaly_detector(self):
        d = SlidingWindowDetector()
        assert isinstance(d, AnomalyDetector)

    def test_pattern_detector_is_anomaly_detector(self):
        d = PatternDetector()
        assert isinstance(d, AnomalyDetector)

    def test_custom_detector_protocol(self):
        class Custom:
            @property
            def detector_id(self) -> str:
                return "custom"

            def observe(self, actor_id, metric_name, value, **kwargs):
                return []

            def reset(self, actor_id=None):
                pass

        assert isinstance(Custom(), AnomalyDetector)


# ═══════════════════════════════════════════════════════════════════════
# SlidingWindowDetector
# ═══════════════════════════════════════════════════════════════════════


class TestSlidingWindowDetector:
    def test_no_rules_returns_empty(self):
        d = SlidingWindowDetector()
        assert d.observe("a", "m", 1.0) == []

    def test_under_threshold_no_drift(self):
        rule = WindowConfig(metric_name="errors", window_seconds=300, max_count=5)
        d = SlidingWindowDetector(rules=[rule])
        for _ in range(5):
            events = d.observe("agent-1", "errors", 1.0)
        assert events == []

    def test_over_threshold_fires(self):
        rule = WindowConfig(
            metric_name="errors",
            window_seconds=300,
            max_count=3,
            severity=DriftSeverity.HIGH,
        )
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        events = []
        for _ in range(5):
            events = d.observe("agent-1", "errors", 1.0)
        assert len(events) == 1
        assert events[0].severity == DriftSeverity.HIGH
        assert events[0].drift_type == DriftType.FREQUENCY_SPIKE

    def test_wildcard_metric(self):
        rule = WindowConfig(metric_name="*", window_seconds=300, max_count=2)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        d.observe("a", "m1", 1.0)
        d.observe("a", "m1", 1.0)
        events = d.observe("a", "m1", 1.0)
        assert len(events) == 1

    def test_different_actors_isolated(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=2)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        for _ in range(3):
            d.observe("a", "x", 1.0)
        events = d.observe("b", "x", 1.0)
        assert events == []

    def test_cooldown_prevents_repeated_fire(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=1)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=9999)
        d.observe("a", "x", 1.0)
        events1 = d.observe("a", "x", 1.0)
        assert len(events1) == 1
        events2 = d.observe("a", "x", 1.0)
        assert events2 == []  # cooldown active

    def test_reset_clears_state(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=1)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        d.observe("a", "x", 1.0)
        d.observe("a", "x", 1.0)
        d.reset()
        events = d.observe("a", "x", 1.0)
        assert events == []

    def test_reset_specific_actor(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=1)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        d.observe("a", "x", 1.0)
        d.observe("a", "x", 1.0)
        d.observe("b", "x", 1.0)
        d.observe("b", "x", 1.0)
        d.reset(actor_id="a")
        # Actor a reset, b still over
        events_a = d.observe("a", "x", 1.0)
        assert events_a == []
        events_b = d.observe("b", "x", 1.0)
        assert len(events_b) == 1

    def test_add_rule(self):
        d = SlidingWindowDetector()
        assert len(d.rules) == 0
        d.add_rule(WindowConfig(metric_name="x", max_count=1))
        assert len(d.rules) == 1

    def test_detector_id(self):
        d = SlidingWindowDetector(detector_id="my-window")
        assert d.detector_id == "my-window"

    def test_event_metadata_contains_detector(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=0)
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0, detector_id="sw")
        events = d.observe("a", "x", 1.0)
        assert len(events) == 1
        assert events[0].metadata["detector"] == "sw"

    def test_description_template(self):
        rule = WindowConfig(
            metric_name="x",
            max_count=0,
            description_template="boom: {count} in {window_seconds}s",
        )
        d = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        events = d.observe("a", "x", 1.0)
        assert "boom: 1" in events[0].description


# ═══════════════════════════════════════════════════════════════════════
# PatternDetector
# ═══════════════════════════════════════════════════════════════════════


class TestPatternDetector:
    def test_no_patterns_returns_empty(self):
        d = PatternDetector()
        assert d.observe("a", "m", 1.0) == []

    def test_simple_pattern_matches(self):
        pattern = OperationPattern(
            steps=[
                ("list_tools", None),
                ("call_tool", None),
            ],
            window_seconds=60,
            severity=DriftSeverity.HIGH,
            description="list then call",
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        events = d.observe("a", "list_tools", 1.0)
        assert events == []
        events = d.observe("a", "call_tool", 1.0)
        assert len(events) == 1
        assert events[0].description == "list then call"

    def test_pattern_with_value_predicate(self):
        pattern = OperationPattern(
            steps=[
                ("error_rate", lambda v: v > 0.5),
                ("error_rate", lambda v: v > 0.5),
            ],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("a", "error_rate", 0.1)  # doesn't match predicate
        d.observe("a", "error_rate", 0.9)  # matches step 0
        events = d.observe("a", "error_rate", 0.8)  # matches step 1
        assert len(events) == 1

    def test_pattern_window_expiry(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=0.01,  # Very short
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("x", "a", 1.0)
        time.sleep(0.02)  # Window expired
        events = d.observe("x", "b", 1.0)
        assert events == []

    def test_different_actors_isolated(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("actor-1", "a", 1.0)
        events = d.observe("actor-2", "b", 1.0)
        assert events == []

    def test_cooldown_prevents_repeated_fire(self):
        pattern = OperationPattern(
            steps=[("x", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=9999)
        events1 = d.observe("a", "x", 1.0)
        assert len(events1) == 1
        events2 = d.observe("a", "x", 1.0)
        assert events2 == []

    def test_reset_clears_progress(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("x", "a", 1.0)
        d.reset()
        events = d.observe("x", "b", 1.0)
        assert events == []

    def test_reset_specific_actor(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("x", "a", 1.0)
        d.observe("y", "a", 1.0)
        d.reset(actor_id="x")
        events_x = d.observe("x", "b", 1.0)
        events_y = d.observe("y", "b", 1.0)
        assert events_x == []
        assert len(events_y) == 1

    def test_add_pattern(self):
        d = PatternDetector()
        assert len(d.patterns) == 0
        d.add_pattern(OperationPattern(steps=[("x", None)]))
        assert len(d.patterns) == 1

    def test_detector_id(self):
        d = PatternDetector(detector_id="my-patterns")
        assert d.detector_id == "my-patterns"

    def test_three_step_pattern(self):
        pattern = OperationPattern(
            steps=[
                ("list_resources", None),
                ("read_resource", None),
                ("read_resource", None),
            ],
            window_seconds=60,
            description="enumeration",
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("a", "list_resources", 1.0)
        d.observe("a", "read_resource", 1.0)
        events = d.observe("a", "read_resource", 1.0)
        assert len(events) == 1
        assert events[0].description == "enumeration"

    def test_wrong_order_no_match(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        d.observe("x", "b", 1.0)
        events = d.observe("x", "a", 1.0)
        assert events == []

    def test_metadata_contains_pattern_id(self):
        pattern = OperationPattern(
            pattern_id="test-pat",
            steps=[("x", None)],
            window_seconds=60,
        )
        d = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        events = d.observe("a", "x", 1.0)
        assert events[0].metadata["pattern_id"] == "test-pat"


# ═══════════════════════════════════════════════════════════════════════
# ActorProfile
# ═══════════════════════════════════════════════════════════════════════


class TestActorProfile:
    def test_default_profile(self):
        p = ActorProfile(actor_id="agent-1")
        assert p.scope_size == 0
        assert p.operation_count == 0
        assert p.known_tools == set()
        assert p.known_resources == set()
        assert p.known_prompts == set()
        assert p.raw_threat_score == 0.0

    def test_scope_size(self):
        p = ActorProfile(actor_id="a")
        p.known_tools.add("tool1")
        p.known_resources.add("res1")
        p.known_resources.add("res2")
        p.known_prompts.add("p1")
        assert p.scope_size == 4


# ═══════════════════════════════════════════════════════════════════════
# ActorProfileManager
# ═══════════════════════════════════════════════════════════════════════


class TestActorProfileManager:
    def test_get_or_create(self):
        mgr = ActorProfileManager()
        p = mgr.get_or_create("agent-1")
        assert p.actor_id == "agent-1"
        assert mgr.actor_count == 1
        # Same profile returned
        assert mgr.get_or_create("agent-1") is p

    def test_get_profile_missing(self):
        mgr = ActorProfileManager()
        assert mgr.get_profile("nope") is None

    def test_record_tool_access(self):
        mgr = ActorProfileManager()
        assert mgr.record_tool_access("a", "tool1") is True  # new
        assert mgr.record_tool_access("a", "tool1") is False  # seen
        assert mgr.record_tool_access("a", "tool2") is True  # new
        p = mgr.get_profile("a")
        assert p is not None
        assert p.known_tools == {"tool1", "tool2"}

    def test_record_resource_access(self):
        mgr = ActorProfileManager()
        assert mgr.record_resource_access("a", "file://x") is True
        assert mgr.record_resource_access("a", "file://x") is False
        p = mgr.get_profile("a")
        assert p is not None
        assert "file://x" in p.known_resources

    def test_record_prompt_access(self):
        mgr = ActorProfileManager()
        assert mgr.record_prompt_access("a", "my-prompt") is True
        assert mgr.record_prompt_access("a", "my-prompt") is False

    def test_record_operation(self):
        mgr = ActorProfileManager()
        mgr.record_operation("a", "custom_op")
        p = mgr.get_profile("a")
        assert p is not None
        assert p.operation_count == 1

    def test_threat_score_starts_zero(self):
        mgr = ActorProfileManager()
        assert mgr.threat_score("nonexistent") == 0.0

    def test_record_drift_bumps_score(self):
        mgr = ActorProfileManager()
        event = DriftEvent(
            actor_id="a",
            severity=DriftSeverity.HIGH,
            description="test",
        )
        mgr.record_drift("a", event)
        score = mgr.threat_score("a")
        assert score > 0.0

    def test_threat_score_decays(self):
        mgr = ActorProfileManager(decay_rate=100.0)  # Very fast decay
        event = DriftEvent(actor_id="a", severity=DriftSeverity.HIGH)
        mgr.record_drift("a", event)
        time.sleep(0.05)
        score = mgr.threat_score("a")
        assert score < 1.0  # Should have decayed significantly

    def test_multiple_drift_events_accumulate(self):
        mgr = ActorProfileManager(decay_rate=0.0)  # No decay
        for _ in range(3):
            mgr.record_drift(
                "a", DriftEvent(actor_id="a", severity=DriftSeverity.MEDIUM)
            )
        score = mgr.threat_score("a")
        assert score == pytest.approx(9.0)  # 3 * 3.0

    def test_scope_expansion_rate(self):
        mgr = ActorProfileManager()
        mgr.record_tool_access("a", "t1")
        mgr.record_resource_access("a", "r1")
        mgr.record_resource_access("a", "r2")
        rate = mgr.scope_expansion_rate("a", window_seconds=300)
        assert rate == 3

    def test_reset_all(self):
        mgr = ActorProfileManager()
        mgr.record_tool_access("a", "t1")
        mgr.record_tool_access("b", "t2")
        mgr.reset()
        assert mgr.actor_count == 0

    def test_reset_specific_actor(self):
        mgr = ActorProfileManager()
        mgr.record_tool_access("a", "t1")
        mgr.record_tool_access("b", "t2")
        mgr.reset("a")
        assert mgr.get_profile("a") is None
        assert mgr.get_profile("b") is not None

    def test_all_profiles(self):
        mgr = ActorProfileManager()
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        profiles = mgr.all_profiles()
        assert len(profiles) == 2


# ═══════════════════════════════════════════════════════════════════════
# BehavioralAnalyzer + detector integration
# ═══════════════════════════════════════════════════════════════════════


class TestAnalyzerDetectorIntegration:
    def test_detector_events_included_in_observe(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=1)
        window = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        analyzer = BehavioralAnalyzer(detectors=[window], min_samples=9999)

        # Under threshold
        events = analyzer.observe("a", "x", 1.0)
        assert events == []

        # Over threshold
        events = analyzer.observe("a", "x", 1.0)
        assert len(events) == 1
        assert events[0].drift_type == DriftType.FREQUENCY_SPIKE

    def test_detector_events_in_drift_history(self):
        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=0)
        window = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        analyzer = BehavioralAnalyzer(detectors=[window], min_samples=9999)

        analyzer.observe("a", "x", 1.0)
        assert analyzer.total_drift_count == 1

    def test_add_detector_at_runtime(self):
        analyzer = BehavioralAnalyzer(min_samples=9999)
        assert len(analyzer.detectors) == 0

        rule = WindowConfig(metric_name="x", window_seconds=300, max_count=0)
        window = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        analyzer.add_detector(window)

        assert len(analyzer.detectors) == 1
        events = analyzer.observe("a", "x", 1.0)
        assert len(events) == 1

    def test_remove_detector(self):
        window = SlidingWindowDetector(
            rules=[WindowConfig(metric_name="x", max_count=0)],
            cooldown_seconds=0,
            detector_id="sw",
        )
        analyzer = BehavioralAnalyzer(detectors=[window], min_samples=9999)
        assert analyzer.remove_detector("sw") is True
        assert len(analyzer.detectors) == 0

    def test_remove_detector_not_found(self):
        analyzer = BehavioralAnalyzer()
        assert analyzer.remove_detector("nope") is False

    def test_pattern_detector_via_analyzer(self):
        pattern = OperationPattern(
            steps=[("a", None), ("b", None)],
            window_seconds=60,
            description="a-then-b",
        )
        detector = PatternDetector(patterns=[pattern], cooldown_seconds=0)
        analyzer = BehavioralAnalyzer(detectors=[detector], min_samples=9999)

        events = analyzer.observe("x", "a", 1.0)
        assert events == []
        events = analyzer.observe("x", "b", 1.0)
        assert len(events) == 1
        assert events[0].description == "a-then-b"

    def test_multiple_detectors(self):
        window = SlidingWindowDetector(
            rules=[WindowConfig(metric_name="x", max_count=0, window_seconds=300)],
            cooldown_seconds=0,
        )
        pattern = PatternDetector(
            patterns=[OperationPattern(steps=[("x", None)], window_seconds=60)],
            cooldown_seconds=0,
        )
        analyzer = BehavioralAnalyzer(detectors=[window, pattern], min_samples=9999)
        events = analyzer.observe("a", "x", 1.0)
        # Both detectors should fire
        assert len(events) == 2

    def test_detector_failure_isolated(self):
        """A broken detector doesn't crash the analyzer."""

        class BrokenDetector:
            @property
            def detector_id(self):
                return "broken"

            def observe(self, *args, **kwargs):
                raise RuntimeError("boom")

            def reset(self, actor_id=None):
                pass

        analyzer = BehavioralAnalyzer(detectors=[BrokenDetector()], min_samples=9999)
        # Should not raise
        events = analyzer.observe("a", "x", 1.0)
        assert events == []

    def test_sigma_and_detector_both_fire(self):
        """Sigma-based detection and pluggable detectors work together."""
        rule = WindowConfig(metric_name="calls", window_seconds=300, max_count=0)
        window = SlidingWindowDetector(rules=[rule], cooldown_seconds=0)
        analyzer = BehavioralAnalyzer(detectors=[window], min_samples=10)

        # Build baseline with slight variation
        for i in range(15):
            analyzer.observe("a", "calls", 10.0 + (i % 3) * 0.1)

        # Extreme value triggers sigma detection + window detection
        events = analyzer.observe("a", "calls", 1000.0)
        # At least one from sigma, at least one from window
        sigma_events = [e for e in events if "detector" not in e.metadata]
        window_events = [
            e for e in events if e.metadata.get("detector") == "sliding-window"
        ]
        assert len(sigma_events) >= 1
        assert len(window_events) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Backwards compatibility
# ═══════════════════════════════════════════════════════════════════════


class TestBackwardsCompatibility:
    def test_analyzer_works_without_detectors(self):
        """Existing code that doesn't pass detectors still works."""
        analyzer = BehavioralAnalyzer(min_samples=5)
        for i in range(10):
            analyzer.observe("a", "m", 10.0 + (i % 3) * 0.1)
        events = analyzer.observe("a", "m", 100.0)
        assert len(events) >= 1

    def test_middleware_works_without_profile_manager(self):
        """ReflexiveMiddleware creates its own profile manager if none given."""
        from fastmcp.server.security.middleware.reflexive import ReflexiveMiddleware

        analyzer = BehavioralAnalyzer()
        mw = ReflexiveMiddleware(analyzer=analyzer)
        assert mw.profile_manager is not None
