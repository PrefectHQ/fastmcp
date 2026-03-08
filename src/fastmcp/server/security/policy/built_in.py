"""Built-in policy providers for common compliance frameworks.

These are starter implementations that can be extended or replaced
with organization-specific policies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.security.policy.provider import (
    PolicyDecision,
    PolicyEvaluationContext,
    PolicyResult,
)

logger = logging.getLogger(__name__)


@dataclass
class TagBasedPolicy:
    """Policy that makes decisions based on component tags.

    Allows defining allow/deny rules for specific tags. Tags not
    covered by any rule are handled by the default_decision.

    Args:
        policy_id: Unique identifier for this policy.
        version: Version string.
        allowed_tags: Tags that should be ALLOWED.
        denied_tags: Tags that should be DENIED.
        default_decision: Decision for unmatched tags.

    Example::

        policy = TagBasedPolicy(
            policy_id="tag-policy",
            allowed_tags={"public", "internal"},
            denied_tags={"deprecated", "unsafe"},
        )
    """

    policy_id: str = "tag-based-policy"
    version: str = "1.0.0"
    allowed_tags: frozenset[str] = field(default_factory=frozenset)
    denied_tags: frozenset[str] = field(default_factory=frozenset)
    default_decision: PolicyDecision = PolicyDecision.ALLOW

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        # Check denied tags first (deny takes precedence)
        denied_matches = context.tags & self.denied_tags
        if denied_matches:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Component has denied tag(s): {', '.join(sorted(denied_matches))}",
                policy_id=self.policy_id,
            )

        # Check if allowed tags are configured and component has them
        if self.allowed_tags:
            allowed_matches = context.tags & self.allowed_tags
            if allowed_matches:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason=f"Component has allowed tag(s): {', '.join(sorted(allowed_matches))}",
                    policy_id=self.policy_id,
                )
            # Has allowed tags configured but component doesn't match any
            if context.tags:
                return PolicyResult(
                    decision=self.default_decision,
                    reason="Component tags do not match any allowed tags",
                    policy_id=self.policy_id,
                )

        return PolicyResult(
            decision=self.default_decision,
            reason="No tag rules matched",
            policy_id=self.policy_id,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class ActionBasedPolicy:
    """Policy that controls access based on action types.

    Args:
        policy_id: Unique identifier for this policy.
        version: Version string.
        allowed_actions: Actions that are ALLOWED (if set, others are denied).
        denied_actions: Actions that are DENIED (others are allowed).
    """

    policy_id: str = "action-based-policy"
    version: str = "1.0.0"
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    denied_actions: frozenset[str] = field(default_factory=frozenset)

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        if self.denied_actions and context.action in self.denied_actions:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Action '{context.action}' is denied by policy",
                policy_id=self.policy_id,
            )

        if self.allowed_actions and context.action not in self.allowed_actions:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Action '{context.action}' is not in the allowed actions list",
                policy_id=self.policy_id,
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"Action '{context.action}' is permitted",
            policy_id=self.policy_id,
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class GDPRPolicy:
    """GDPR compliance policy stub.

    Enforces basic GDPR principles: denies access to components tagged
    with personal data categories unless the actor has appropriate
    consent or legal basis declared in metadata.

    This is a starting point; extend with your organization's specific
    GDPR requirements.
    """

    policy_id: str = "gdpr-v1"
    version: str = "1.0.0"
    personal_data_tags: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"pii", "personal_data", "sensitive_data", "gdpr_regulated"}
        )
    )
    valid_legal_bases: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "consent",
                "contract",
                "legal_obligation",
                "vital_interests",
                "public_interest",
                "legitimate_interests",
            }
        )
    )

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        # Check if component handles personal data
        pd_tags = context.tags & self.personal_data_tags
        if not pd_tags:
            return PolicyResult(
                decision=PolicyDecision.DEFER,
                reason="No personal data tags; GDPR not applicable",
                policy_id=self.policy_id,
            )

        # Require legal basis in metadata
        legal_basis = context.metadata.get("legal_basis")
        if not legal_basis:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Personal data access requires legal basis (tags: {', '.join(sorted(pd_tags))})",
                policy_id=self.policy_id,
                constraints=["legal_basis_required"],
            )

        if legal_basis not in self.valid_legal_bases:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Invalid legal basis '{legal_basis}' for personal data access",
                policy_id=self.policy_id,
                constraints=["valid_legal_basis_required"],
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"GDPR: Access permitted under '{legal_basis}'",
            policy_id=self.policy_id,
            constraints=[f"legal_basis:{legal_basis}"],
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version


@dataclass
class HIPAAPolicy:
    """HIPAA compliance policy stub.

    Enforces basic HIPAA principles: denies access to components tagged
    with protected health information (PHI) unless the actor has
    appropriate authorization.

    This is a starting point; extend with your organization's specific
    HIPAA requirements.
    """

    policy_id: str = "hipaa-v1"
    version: str = "1.0.0"
    phi_tags: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"phi", "health_data", "medical_record", "hipaa_regulated"}
        )
    )
    authorized_roles: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"healthcare_provider", "health_plan", "healthcare_clearinghouse", "business_associate"}
        )
    )

    async def evaluate(self, context: PolicyEvaluationContext) -> PolicyResult:
        # Check if component handles PHI
        phi_match = context.tags & self.phi_tags
        if not phi_match:
            return PolicyResult(
                decision=PolicyDecision.DEFER,
                reason="No PHI tags; HIPAA not applicable",
                policy_id=self.policy_id,
            )

        # Require authorized role
        actor_role = context.metadata.get("actor_role")
        if not actor_role:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"PHI access requires authorized role (tags: {', '.join(sorted(phi_match))})",
                policy_id=self.policy_id,
                constraints=["authorized_role_required"],
            )

        if actor_role not in self.authorized_roles:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"Role '{actor_role}' is not authorized for PHI access",
                policy_id=self.policy_id,
                constraints=["valid_role_required"],
            )

        # Check minimum necessary principle
        purpose = context.metadata.get("purpose")
        if not purpose:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                reason="PHI access requires stated purpose (minimum necessary principle)",
                policy_id=self.policy_id,
                constraints=["purpose_required"],
            )

        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            reason=f"HIPAA: Access permitted for role '{actor_role}' with purpose '{purpose}'",
            policy_id=self.policy_id,
            constraints=[f"role:{actor_role}", f"purpose:{purpose}"],
        )

    async def get_policy_id(self) -> str:
        return self.policy_id

    async def get_policy_version(self) -> str:
        return self.version
