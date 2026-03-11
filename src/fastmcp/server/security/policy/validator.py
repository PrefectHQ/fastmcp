"""Policy validation for SecureMCP.

Validates policies at creation time to catch configuration errors,
contradictions, and structural issues before they reach production.

Two levels of validation:

1. **Schema validation** — Structural checks on declarative policy dicts
   (required fields, valid types, valid values, composition rules).

2. **Semantic validation** — Logical consistency checks across providers
   (contradicting allow/deny lists, shadowed rules, depth limits).

Example::

    from fastmcp.server.security.policy.validator import PolicyValidator

    validator = PolicyValidator()

    # Validate a declarative policy dict
    result = validator.validate_declarative(policy_dict)
    if not result.valid:
        for err in result.errors:
            print(f"ERROR: {err}")

    # Validate a list of providers for contradictions
    result = validator.validate_providers([allowlist, denylist, rbac])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Severity of a validation finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class ValidationFinding:
    """A single validation finding.

    Attributes:
        severity: How serious the finding is.
        message: Human-readable description.
        path: Dot-separated path to the problematic config key.
        code: Machine-readable error code for programmatic handling.
    """

    severity: ValidationSeverity
    message: str
    path: str = ""
    code: str = ""


@dataclass
class ValidationResult:
    """Result of a policy validation run.

    Attributes:
        valid: True if no ERROR-level findings were found.
        findings: All findings (errors, warnings, and info).
    """

    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(f.severity == ValidationSeverity.ERROR for f in self.findings)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == ValidationSeverity.WARNING]

    def to_dict(self) -> dict[str, Any]:
        """Export as JSON-serializable dict."""
        return {
            "valid": self.valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "findings": [
                {
                    "severity": f.severity.value,
                    "message": f.message,
                    "path": f.path,
                    "code": f.code,
                }
                for f in self.findings
            ],
        }


# ── Known policy types and their fields ─────────────────────────

_VALID_POLICY_TYPES = {
    "allowlist",
    "denylist",
    "rbac",
    "role_based",
    "rate_limit",
    "time_based",
    "temporal",
    "abac",
    "attribute_based",
    "resource_scoped",
    "allow_all",
    "deny_all",
}

_VALID_COMPOSITIONS = {
    "all_of",
    "allof",
    "all",
    "any_of",
    "anyof",
    "any",
    "first_match",
    "firstmatch",
    "not",
}

_VALID_DECISIONS = {"allow", "deny", "defer"}

_POLICY_REQUIRED_FIELDS: dict[str, list[str]] = {
    "allowlist": ["allowed"],
    "denylist": ["denied"],
    "rbac": ["role_mappings"],
    "role_based": ["role_mappings"],
    "rate_limit": [],
    "time_based": [],
    "temporal": [],
    "abac": [],
    "attribute_based": [],
    "resource_scoped": ["resource_rules"],
    "allow_all": [],
    "deny_all": [],
}


class PolicyValidator:
    """Validates policy configurations before deployment.

    Catches structural errors, type mismatches, logical contradictions,
    and potentially dangerous configurations.

    Args:
        max_composition_depth: Maximum nesting depth for composite policies.
        max_providers: Maximum number of providers in a single engine.
    """

    def __init__(
        self,
        *,
        max_composition_depth: int = 10,
        max_providers: int = 50,
    ) -> None:
        self.max_composition_depth = max_composition_depth
        self.max_providers = max_providers

    # ── Schema validation ──────────────────────────────────────

    def validate_declarative(
        self,
        config: dict[str, Any],
        *,
        path: str = "",
    ) -> ValidationResult:
        """Validate a declarative policy config dict.

        Checks structure, types, required fields, and nesting depth.

        Args:
            config: The policy dict to validate.
            path: Parent path for nested validation.

        Returns:
            A ValidationResult with all findings.
        """
        result = ValidationResult()
        self._validate_node(config, result, path=path, depth=0)
        return result

    def _validate_node(
        self,
        config: dict[str, Any],
        result: ValidationResult,
        *,
        path: str,
        depth: int,
    ) -> None:
        """Validate a single policy node (leaf or composite)."""
        if depth > self.max_composition_depth:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"Composition nesting depth exceeds maximum ({self.max_composition_depth})",
                    path=path,
                    code="E_DEPTH_EXCEEDED",
                )
            )
            return

        if not isinstance(config, dict):
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"Expected a dict, got {type(config).__name__}",
                    path=path,
                    code="E_INVALID_TYPE",
                )
            )
            return

        # Check if this is a composite policy
        is_composite = "composition" in config or "policies" in config

        if is_composite:
            self._validate_composite(config, result, path=path, depth=depth)
        else:
            self._validate_leaf(config, result, path=path)

    def _validate_leaf(
        self,
        config: dict[str, Any],
        result: ValidationResult,
        *,
        path: str,
    ) -> None:
        """Validate a leaf policy node."""
        policy_type = config.get("type", "")

        if not policy_type:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message="Missing required 'type' field",
                    path=path or "root",
                    code="E_MISSING_TYPE",
                )
            )
            return

        if policy_type not in _VALID_POLICY_TYPES:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"Unknown policy type: {policy_type!r}. "
                    f"Valid types: {sorted(_VALID_POLICY_TYPES)}",
                    path=f"{path}.type" if path else "type",
                    code="E_UNKNOWN_TYPE",
                )
            )
            return

        # Check required fields
        required = _POLICY_REQUIRED_FIELDS.get(policy_type, [])
        for field_name in required:
            if field_name not in config:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message=f"Policy type '{policy_type}' requires field '{field_name}'",
                        path=f"{path}.{field_name}" if path else field_name,
                        code="E_MISSING_FIELD",
                    )
                )

        # Type-specific validation
        self._validate_type_specific(config, policy_type, result, path=path)

    def _validate_type_specific(
        self,
        config: dict[str, Any],
        policy_type: str,
        result: ValidationResult,
        *,
        path: str,
    ) -> None:
        """Run type-specific validation for leaf policies."""
        if policy_type in ("allowlist",):
            allowed = config.get("allowed", [])
            if not isinstance(allowed, list):
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'allowed' must be a list of strings",
                        path=f"{path}.allowed" if path else "allowed",
                        code="E_INVALID_FIELD_TYPE",
                    )
                )
            elif len(allowed) == 0:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        message="Allowlist is empty — no resources will be allowed by this policy",
                        path=f"{path}.allowed" if path else "allowed",
                        code="W_EMPTY_ALLOWLIST",
                    )
                )

        elif policy_type in ("denylist",):
            denied = config.get("denied", [])
            if not isinstance(denied, list):
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'denied' must be a list of strings",
                        path=f"{path}.denied" if path else "denied",
                        code="E_INVALID_FIELD_TYPE",
                    )
                )
            elif len(denied) == 0:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.WARNING,
                        message="Denylist is empty — no resources will be denied",
                        path=f"{path}.denied" if path else "denied",
                        code="W_EMPTY_DENYLIST",
                    )
                )

        elif policy_type in ("rbac", "role_based"):
            mappings = config.get("role_mappings", {})
            if not isinstance(mappings, dict):
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'role_mappings' must be a dict mapping role names to action lists",
                        path=f"{path}.role_mappings" if path else "role_mappings",
                        code="E_INVALID_FIELD_TYPE",
                    )
                )
            else:
                for role, actions in mappings.items():
                    if not isinstance(actions, list):
                        result.findings.append(
                            ValidationFinding(
                                severity=ValidationSeverity.ERROR,
                                message=f"Actions for role '{role}' must be a list",
                                path=f"{path}.role_mappings.{role}"
                                if path
                                else f"role_mappings.{role}",
                                code="E_INVALID_FIELD_TYPE",
                            )
                        )
            decision = config.get("default_decision")
            if decision is not None and decision.lower() not in _VALID_DECISIONS:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message=f"Invalid default_decision: {decision!r}. Must be one of {sorted(_VALID_DECISIONS)}",
                        path=f"{path}.default_decision" if path else "default_decision",
                        code="E_INVALID_DECISION",
                    )
                )

        elif policy_type == "rate_limit":
            max_req = config.get("max_requests", 100)
            if not isinstance(max_req, int) or max_req <= 0:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'max_requests' must be a positive integer",
                        path=f"{path}.max_requests" if path else "max_requests",
                        code="E_INVALID_FIELD_VALUE",
                    )
                )
            window = config.get("window_seconds", 3600)
            if not isinstance(window, int) or window <= 0:
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'window_seconds' must be a positive integer",
                        path=f"{path}.window_seconds" if path else "window_seconds",
                        code="E_INVALID_FIELD_VALUE",
                    )
                )

        elif policy_type in ("time_based", "temporal"):
            days = config.get("allowed_days")
            if days is not None:
                if not isinstance(days, list):
                    result.findings.append(
                        ValidationFinding(
                            severity=ValidationSeverity.ERROR,
                            message="'allowed_days' must be a list of integers (0=Mon..6=Sun)",
                            path=f"{path}.allowed_days" if path else "allowed_days",
                            code="E_INVALID_FIELD_TYPE",
                        )
                    )
                else:
                    for d in days:
                        if not isinstance(d, int) or d < 0 or d > 6:
                            result.findings.append(
                                ValidationFinding(
                                    severity=ValidationSeverity.ERROR,
                                    message=f"Invalid day value: {d}. Must be 0-6 (Mon-Sun)",
                                    path=f"{path}.allowed_days"
                                    if path
                                    else "allowed_days",
                                    code="E_INVALID_FIELD_VALUE",
                                )
                            )
                            break

            for hour_field in ("start_hour", "end_hour"):
                hour = config.get(hour_field)
                if hour is not None:
                    if not isinstance(hour, int) or hour < 0 or hour > 23:
                        result.findings.append(
                            ValidationFinding(
                                severity=ValidationSeverity.ERROR,
                                message=f"'{hour_field}' must be 0-23",
                                path=f"{path}.{hour_field}" if path else hour_field,
                                code="E_INVALID_FIELD_VALUE",
                            )
                        )

        elif policy_type == "resource_scoped":
            rules = config.get("resource_rules", {})
            if not isinstance(rules, dict):
                result.findings.append(
                    ValidationFinding(
                        severity=ValidationSeverity.ERROR,
                        message="'resource_rules' must be a dict mapping resource IDs to sub-policies",
                        path=f"{path}.resource_rules" if path else "resource_rules",
                        code="E_INVALID_FIELD_TYPE",
                    )
                )

    def _validate_composite(
        self,
        config: dict[str, Any],
        result: ValidationResult,
        *,
        path: str,
        depth: int,
    ) -> None:
        """Validate a composite policy node."""
        composition = config.get("composition", "all_of")
        if composition.lower() not in _VALID_COMPOSITIONS:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"Unknown composition: {composition!r}. "
                    f"Valid: {sorted(_VALID_COMPOSITIONS)}",
                    path=f"{path}.composition" if path else "composition",
                    code="E_UNKNOWN_COMPOSITION",
                )
            )
            return

        children = config.get("policies", [])
        if not isinstance(children, list):
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message="'policies' must be a list",
                    path=f"{path}.policies" if path else "policies",
                    code="E_INVALID_FIELD_TYPE",
                )
            )
            return

        if len(children) == 0:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    message="Composite policy has no children",
                    path=f"{path}.policies" if path else "policies",
                    code="W_EMPTY_COMPOSITION",
                )
            )

        # 'not' requires exactly one child
        normalized = composition.lower()
        if normalized == "not" and len(children) != 1:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"'not' composition requires exactly 1 child, got {len(children)}",
                    path=f"{path}.policies" if path else "policies",
                    code="E_NOT_SINGLE_CHILD",
                )
            )

        # Validate each child
        for i, child in enumerate(children):
            child_path = f"{path}.policies[{i}]" if path else f"policies[{i}]"
            self._validate_node(child, result, path=child_path, depth=depth + 1)

    # ── Semantic validation ────────────────────────────────────

    def validate_providers(
        self,
        providers: list[Any],
    ) -> ValidationResult:
        """Validate a list of instantiated providers for logical issues.

        Checks for:
        - Contradicting allowlist/denylist patterns
        - Shadowed rules (rules that can never fire)
        - Too many providers
        - Redundant policies

        Args:
            providers: List of PolicyProvider instances.

        Returns:
            A ValidationResult with semantic findings.
        """
        result = ValidationResult()

        if len(providers) > self.max_providers:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    message=f"Provider count ({len(providers)}) exceeds recommended maximum ({self.max_providers})",
                    path="providers",
                    code="W_TOO_MANY_PROVIDERS",
                )
            )

        # Collect allowlist and denylist patterns
        from fastmcp.server.security.policy.policies.allowlist import (
            AllowlistPolicy,
            DenylistPolicy,
        )

        allow_patterns: set[str] = set()
        deny_patterns: set[str] = set()
        has_allow_all = False
        has_deny_all = False

        for i, provider in enumerate(providers):
            from fastmcp.server.security.policy.provider import (
                AllowAllPolicy,
                DenyAllPolicy,
            )

            if isinstance(provider, AllowAllPolicy):
                has_allow_all = True
            elif isinstance(provider, DenyAllPolicy):
                has_deny_all = True
            elif isinstance(provider, AllowlistPolicy):
                allow_patterns.update(provider.allowed)
            elif isinstance(provider, DenylistPolicy):
                deny_patterns.update(provider.denied)

        # Check for contradictions: exact match patterns in both allow and deny
        overlap = allow_patterns & deny_patterns
        if overlap:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message=f"Contradicting rules: patterns {sorted(overlap)} appear in both allowlist and denylist. "
                    f"With AND logic the denylist will always win, making the allowlist entries ineffective.",
                    path="providers",
                    code="E_CONTRADICTING_RULES",
                )
            )

        # AllowAll + DenyAll together is contradictory
        if has_allow_all and has_deny_all:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.ERROR,
                    message="Both AllowAllPolicy and DenyAllPolicy are configured. "
                    "DenyAllPolicy will always win with AND logic.",
                    path="providers",
                    code="E_ALLOW_DENY_ALL",
                )
            )

        # DenyAll makes everything else redundant
        if has_deny_all and len(providers) > 1:
            result.findings.append(
                ValidationFinding(
                    severity=ValidationSeverity.WARNING,
                    message="DenyAllPolicy is configured alongside other policies. "
                    "With AND logic, all requests will be denied regardless of other policies.",
                    path="providers",
                    code="W_DENY_ALL_SHADOWS",
                )
            )

        return result

    def validate_full(
        self,
        config: dict[str, Any] | None = None,
        providers: list[Any] | None = None,
    ) -> ValidationResult:
        """Run both schema and semantic validation.

        Args:
            config: Optional declarative config dict.
            providers: Optional list of instantiated providers.

        Returns:
            Combined ValidationResult with all findings.
        """
        result = ValidationResult()

        if config is not None:
            schema_result = self.validate_declarative(config)
            result.findings.extend(schema_result.findings)

        if providers is not None:
            semantic_result = self.validate_providers(providers)
            result.findings.extend(semantic_result.findings)

        return result
