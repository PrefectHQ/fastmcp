"""Tests for the EscalationEngine."""

from __future__ import annotations

import time

from fastmcp.server.security.reflexive.analyzer import EscalationEngine
from fastmcp.server.security.reflexive.models import (
    DriftEvent,
    DriftSeverity,
    EscalationAction,
    EscalationRule,
)


class TestEscalationEngineBasics:
    def test_empty_engine(self):
        engine = EscalationEngine()
        assert engine.escalation_count == 0

    def test_evaluate_no_rules(self):
        engine = EscalationEngine()
        event = DriftEvent(severity=DriftSeverity.CRITICAL)
        actions = engine.evaluate(event)
        assert actions == []

    def test_evaluate_matching_rule(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.MEDIUM,
            action=EscalationAction.ALERT,
            cooldown_seconds=0,
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(
            severity=DriftSeverity.HIGH,
            actor_id="agent-1",
        )
        actions = engine.evaluate(event)
        assert len(actions) == 1
        assert actions[0][0] == EscalationAction.ALERT

    def test_evaluate_non_matching_severity(self):
        rule = EscalationRule(
            min_severity=DriftSeverity.HIGH,
            action=EscalationAction.SUSPEND_AGENT,
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(severity=DriftSeverity.LOW, actor_id="a1")
        actions = engine.evaluate(event)
        assert actions == []

    def test_multiple_rules_triggered(self):
        rules = [
            EscalationRule(
                rule_id="r1",
                min_severity=DriftSeverity.LOW,
                action=EscalationAction.LOG,
                cooldown_seconds=0,
            ),
            EscalationRule(
                rule_id="r2",
                min_severity=DriftSeverity.LOW,
                action=EscalationAction.ALERT,
                cooldown_seconds=0,
            ),
        ]
        engine = EscalationEngine(rules=rules)
        event = DriftEvent(severity=DriftSeverity.MEDIUM, actor_id="a1")
        actions = engine.evaluate(event)
        assert len(actions) == 2


class TestEscalationEngineCooldown:
    def test_cooldown_blocks_rapid_fire(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.LOW,
            action=EscalationAction.ALERT,
            cooldown_seconds=10.0,
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(severity=DriftSeverity.MEDIUM, actor_id="a1")

        # First trigger works
        actions1 = engine.evaluate(event)
        assert len(actions1) == 1

        # Second trigger within cooldown should not fire
        actions2 = engine.evaluate(event)
        assert actions2 == []

    def test_cooldown_expires(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.LOW,
            action=EscalationAction.ALERT,
            cooldown_seconds=0.1,  # Very short cooldown
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(severity=DriftSeverity.MEDIUM, actor_id="a1")

        actions1 = engine.evaluate(event)
        assert len(actions1) == 1

        time.sleep(0.15)

        actions2 = engine.evaluate(event)
        assert len(actions2) == 1


class TestEscalationEngineThreshold:
    def test_threshold_count(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.LOW,
            action=EscalationAction.SUSPEND_AGENT,
            threshold_count=3,
            cooldown_seconds=0,
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(severity=DriftSeverity.HIGH, actor_id="a1")

        # First two don't trigger
        assert engine.evaluate(event) == []
        assert engine.evaluate(event) == []

        # Third triggers
        actions = engine.evaluate(event)
        assert len(actions) == 1
        assert actions[0][0] == EscalationAction.SUSPEND_AGENT


class TestEscalationEngineManagement:
    def test_add_rule(self):
        engine = EscalationEngine()
        rule = EscalationRule(action=EscalationAction.LOG)
        engine.add_rule(rule)
        assert len(engine.rules) == 1

    def test_remove_rule(self):
        rule = EscalationRule(rule_id="to-remove")
        engine = EscalationEngine(rules=[rule])
        assert engine.remove_rule("to-remove")
        assert len(engine.rules) == 0

    def test_remove_rule_not_found(self):
        engine = EscalationEngine()
        assert not engine.remove_rule("nonexistent")

    def test_escalation_history(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.LOW,
            action=EscalationAction.ALERT,
            cooldown_seconds=0,
        )
        engine = EscalationEngine(rules=[rule])
        event = DriftEvent(severity=DriftSeverity.HIGH, actor_id="a1")
        engine.evaluate(event)

        history = engine.get_escalation_history()
        assert len(history) == 1
        assert history[0][2] == EscalationAction.ALERT

    def test_escalation_count(self):
        rule = EscalationRule(
            rule_id="r1",
            min_severity=DriftSeverity.LOW,
            action=EscalationAction.LOG,
            cooldown_seconds=0,
        )
        engine = EscalationEngine(rules=[rule])
        for _ in range(3):
            engine.evaluate(DriftEvent(severity=DriftSeverity.HIGH, actor_id="a1"))
        assert engine.escalation_count == 3
