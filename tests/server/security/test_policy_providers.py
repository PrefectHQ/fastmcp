"""Tests for built-in policy providers."""

from __future__ import annotations

import pytest

from fastmcp.server.security.policy.built_in import (
    ActionBasedPolicy,
    GDPRPolicy,
    HIPAAPolicy,
    TagBasedPolicy,
)
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyDecision,
    PolicyEvaluationContext,
)


def _ctx(
    tags: frozenset[str] | None = None,
    action: str = "call_tool",
    metadata: dict | None = None,
) -> PolicyEvaluationContext:
    return PolicyEvaluationContext(
        actor_id="test",
        action=action,
        resource_id="test_resource",
        tags=tags or frozenset(),
        metadata=metadata or {},
    )


# ── TagBasedPolicy ───────────────────────────────────────────────────


class TestTagBasedPolicy:
    async def test_denied_tag_denies(self):
        policy = TagBasedPolicy(denied_tags=frozenset({"unsafe"}))
        result = await policy.evaluate(_ctx(tags=frozenset({"unsafe"})))
        assert result.decision == PolicyDecision.DENY

    async def test_allowed_tag_allows(self):
        policy = TagBasedPolicy(allowed_tags=frozenset({"public"}))
        result = await policy.evaluate(_ctx(tags=frozenset({"public"})))
        assert result.decision == PolicyDecision.ALLOW

    async def test_denied_takes_precedence_over_allowed(self):
        policy = TagBasedPolicy(
            allowed_tags=frozenset({"public"}),
            denied_tags=frozenset({"unsafe"}),
        )
        result = await policy.evaluate(_ctx(tags=frozenset({"public", "unsafe"})))
        assert result.decision == PolicyDecision.DENY

    async def test_no_matching_tags_uses_default(self):
        policy = TagBasedPolicy(
            allowed_tags=frozenset({"admin"}),
            default_decision=PolicyDecision.DENY,
        )
        result = await policy.evaluate(_ctx(tags=frozenset({"user"})))
        assert result.decision == PolicyDecision.DENY

    async def test_no_tags_no_rules_uses_default(self):
        policy = TagBasedPolicy()
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.ALLOW

    async def test_get_policy_id(self):
        policy = TagBasedPolicy(policy_id="my-tag-policy")
        assert await policy.get_policy_id() == "my-tag-policy"


# ── ActionBasedPolicy ────────────────────────────────────────────────


class TestActionBasedPolicy:
    async def test_denied_action(self):
        policy = ActionBasedPolicy(denied_actions=frozenset({"call_tool"}))
        result = await policy.evaluate(_ctx(action="call_tool"))
        assert result.decision == PolicyDecision.DENY

    async def test_allowed_action(self):
        policy = ActionBasedPolicy(allowed_actions=frozenset({"call_tool"}))
        result = await policy.evaluate(_ctx(action="call_tool"))
        assert result.decision == PolicyDecision.ALLOW

    async def test_action_not_in_allowed_list(self):
        policy = ActionBasedPolicy(
            allowed_actions=frozenset({"read_resource"})
        )
        result = await policy.evaluate(_ctx(action="call_tool"))
        assert result.decision == PolicyDecision.DENY

    async def test_no_restrictions_allows(self):
        policy = ActionBasedPolicy()
        result = await policy.evaluate(_ctx(action="anything"))
        assert result.decision == PolicyDecision.ALLOW


# ── GDPRPolicy ───────────────────────────────────────────────────────


class TestGDPRPolicy:
    async def test_non_personal_data_defers(self):
        policy = GDPRPolicy()
        result = await policy.evaluate(_ctx(tags=frozenset({"public"})))
        assert result.decision == PolicyDecision.DEFER

    async def test_personal_data_without_basis_denies(self):
        policy = GDPRPolicy()
        result = await policy.evaluate(_ctx(tags=frozenset({"pii"})))
        assert result.decision == PolicyDecision.DENY
        assert "legal basis" in result.reason.lower()

    async def test_personal_data_with_valid_basis_allows(self):
        policy = GDPRPolicy()
        result = await policy.evaluate(
            _ctx(
                tags=frozenset({"pii"}),
                metadata={"legal_basis": "consent"},
            )
        )
        assert result.decision == PolicyDecision.ALLOW

    async def test_personal_data_with_invalid_basis_denies(self):
        policy = GDPRPolicy()
        result = await policy.evaluate(
            _ctx(
                tags=frozenset({"personal_data"}),
                metadata={"legal_basis": "because_i_want_to"},
            )
        )
        assert result.decision == PolicyDecision.DENY


# ── HIPAAPolicy ──────────────────────────────────────────────────────


class TestHIPAAPolicy:
    async def test_non_phi_defers(self):
        policy = HIPAAPolicy()
        result = await policy.evaluate(_ctx(tags=frozenset({"public"})))
        assert result.decision == PolicyDecision.DEFER

    async def test_phi_without_role_denies(self):
        policy = HIPAAPolicy()
        result = await policy.evaluate(_ctx(tags=frozenset({"phi"})))
        assert result.decision == PolicyDecision.DENY

    async def test_phi_with_unauthorized_role_denies(self):
        policy = HIPAAPolicy()
        result = await policy.evaluate(
            _ctx(
                tags=frozenset({"phi"}),
                metadata={"actor_role": "janitor"},
            )
        )
        assert result.decision == PolicyDecision.DENY

    async def test_phi_with_authorized_role_but_no_purpose_denies(self):
        policy = HIPAAPolicy()
        result = await policy.evaluate(
            _ctx(
                tags=frozenset({"phi"}),
                metadata={"actor_role": "healthcare_provider"},
            )
        )
        assert result.decision == PolicyDecision.DENY
        assert "purpose" in result.reason.lower()

    async def test_phi_with_role_and_purpose_allows(self):
        policy = HIPAAPolicy()
        result = await policy.evaluate(
            _ctx(
                tags=frozenset({"phi"}),
                metadata={
                    "actor_role": "healthcare_provider",
                    "purpose": "patient_treatment",
                },
            )
        )
        assert result.decision == PolicyDecision.ALLOW


# ── AllowAll / DenyAll ───────────────────────────────────────────────


class TestSimplePolicies:
    async def test_allow_all(self):
        policy = AllowAllPolicy()
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.ALLOW
        assert await policy.get_policy_id() == "allow-all"
        assert await policy.get_policy_version() == "1.0.0"

    async def test_deny_all(self):
        policy = DenyAllPolicy()
        result = await policy.evaluate(_ctx())
        assert result.decision == PolicyDecision.DENY
        assert await policy.get_policy_id() == "deny-all"
