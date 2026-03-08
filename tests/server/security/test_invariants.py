"""Tests for SecureMCP invariant verification system."""

from __future__ import annotations

import pytest

from fastmcp.server.security.policy.invariants import (
    ExpressionInvariantVerifier,
    Invariant,
    InvariantRegistry,
    InvariantSeverity,
)


def _invariant(
    expression: str,
    invariant_id: str = "test-inv",
    severity: InvariantSeverity = InvariantSeverity.MEDIUM,
) -> Invariant:
    return Invariant(
        id=invariant_id,
        description=f"Test invariant: {expression}",
        expression=expression,
        severity=severity,
    )


# ── ExpressionInvariantVerifier ──────────────────────────────────────


class TestExpressionVerifier:
    def test_simple_true_expression(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("x > 0")
        result = verifier.verify(inv, {"x": 5})
        assert result.satisfied is True
        assert result.counter_example is None

    def test_simple_false_expression(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("x > 0")
        result = verifier.verify(inv, {"x": -1})
        assert result.satisfied is False
        assert result.counter_example is not None

    def test_all_items_check(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("all(v > 0 for v in values)")
        result = verifier.verify(inv, {"values": [1, 2, 3]})
        assert result.satisfied is True

    def test_all_items_check_fails(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("all(v > 0 for v in values)")
        result = verifier.verify(inv, {"values": [1, -2, 3]})
        assert result.satisfied is False

    def test_len_check(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("len(items) <= max_size")
        result = verifier.verify(inv, {"items": [1, 2], "max_size": 5})
        assert result.satisfied is True

    def test_invalid_expression_returns_unsatisfied(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("this is not valid python!!!")
        result = verifier.verify(inv, {})
        assert result.satisfied is False
        assert result.counter_example is not None
        assert "error" in result.counter_example

    def test_missing_variable_returns_unsatisfied(self):
        verifier = ExpressionInvariantVerifier()
        inv = _invariant("x > 0")
        result = verifier.verify(inv, {})
        assert result.satisfied is False

    def test_verifier_id(self):
        verifier = ExpressionInvariantVerifier(verifier_id="my-verifier")
        assert verifier.get_verifier_id() == "my-verifier"

    def test_restricted_builtins(self):
        verifier = ExpressionInvariantVerifier()
        # Should not be able to import
        inv = _invariant("__import__('os').system('echo hacked')")
        result = verifier.verify(inv, {})
        assert result.satisfied is False


# ── InvariantRegistry ────────────────────────────────────────────────


class TestInvariantRegistry:
    async def test_register_and_verify_all(self):
        registry = InvariantRegistry()
        registry.register(_invariant("x > 0", invariant_id="inv-1"))
        registry.register(_invariant("y < 10", invariant_id="inv-2"))

        results = await registry.verify_all({"x": 5, "y": 3})
        assert len(results) == 2
        assert all(r.satisfied for r in results)

    async def test_verify_one(self):
        registry = InvariantRegistry()
        registry.register(_invariant("x > 0", invariant_id="inv-1"))

        result = await registry.verify_one("inv-1", {"x": 5})
        assert result.satisfied is True

    async def test_verify_one_not_found_raises(self):
        registry = InvariantRegistry()
        with pytest.raises(KeyError, match="inv-999"):
            await registry.verify_one("inv-999", {})

    async def test_unregister(self):
        registry = InvariantRegistry()
        inv = _invariant("x > 0", invariant_id="inv-1")
        registry.register(inv)
        assert len(registry.invariants) == 1

        removed = registry.unregister("inv-1")
        assert removed is inv
        assert len(registry.invariants) == 0

    async def test_unregister_nonexistent_returns_none(self):
        registry = InvariantRegistry()
        assert registry.unregister("nonexistent") is None

    async def test_violations_tracked(self):
        registry = InvariantRegistry()
        registry.register(_invariant("x > 0", invariant_id="inv-1"))

        await registry.verify_all({"x": -1})
        violations = registry.get_violations()
        assert len(violations) == 1
        assert violations[0].invariant.id == "inv-1"

    async def test_recent_results_limited(self):
        registry = InvariantRegistry()
        registry.register(_invariant("True", invariant_id="always-true"))

        for _ in range(10):
            await registry.verify_all({})

        assert len(registry.recent_results) == 10
