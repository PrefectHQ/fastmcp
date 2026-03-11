"""Declarative policy definitions from YAML/JSON.

Load and build composite policies from structured config files or dicts,
without writing Python code.

Supported formats: YAML (``.yaml``/``.yml``), JSON (``.json``), or raw
Python dicts.

Example YAML::

    policy_id: production-policy
    version: "2.0"
    composition: all_of
    policies:
      - type: allowlist
        allowed: ["weather-*", "translate"]
      - type: denylist
        denied: ["admin-*", "debug-*"]
      - type: rbac
        role_mappings:
          admin: ["*"]
          viewer: ["call_tool", "read_resource"]
      - type: rate_limit
        max_requests: 100
        window_seconds: 3600
      - type: time_based
        allowed_days: [0, 1, 2, 3, 4]
        start_hour: 9
        end_hour: 17

Example usage::

    from fastmcp.server.security.policy.declarative import load_policy

    # From a YAML file
    policy = load_policy("policies/production.yaml")

    # From a dict
    policy = load_policy({
        "composition": "all_of",
        "policies": [
            {"type": "allowlist", "allowed": ["safe-*"]},
        ],
    })

    # Use with PolicyEngine
    engine = PolicyEngine(providers=[policy])
"""

from __future__ import annotations

import json
import logging
from datetime import time
from pathlib import Path
from typing import Any

from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.policies.abac import AttributeBasedPolicy
from fastmcp.server.security.policy.policies.allowlist import (
    AllowlistPolicy,
    DenylistPolicy,
)
from fastmcp.server.security.policy.policies.rate_limit import RateLimitPolicy
from fastmcp.server.security.policy.policies.rbac import RoleBasedPolicy
from fastmcp.server.security.policy.policies.resource_scoped import (
    ResourceScopedPolicy,
)
from fastmcp.server.security.policy.policies.temporal import TimeBasedPolicy
from fastmcp.server.security.policy.provider import (
    AllowAllPolicy,
    DenyAllPolicy,
    PolicyDecision,
    PolicyProvider,
)

logger = logging.getLogger(__name__)


# ── Policy type registry ───────────────────────────────────────────


def _build_allowlist(config: dict[str, Any]) -> AllowlistPolicy:
    return AllowlistPolicy(
        allowed=set(config.get("allowed", [])),
        policy_id=config.get("policy_id", "allowlist-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_denylist(config: dict[str, Any]) -> DenylistPolicy:
    return DenylistPolicy(
        denied=set(config.get("denied", [])),
        policy_id=config.get("policy_id", "denylist-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_rbac(config: dict[str, Any]) -> RoleBasedPolicy:
    raw = config.get("role_mappings", {})
    mappings = {role: set(actions) for role, actions in raw.items()}
    return RoleBasedPolicy(
        role_mappings=mappings,
        policy_id=config.get("policy_id", "rbac-policy"),
        version=config.get("version", "1.0.0"),
        default_decision=_parse_decision(
            config.get("default_decision", "deny"),
        ),
    )


def _build_rate_limit(config: dict[str, Any]) -> RateLimitPolicy:
    return RateLimitPolicy(
        max_requests=config.get("max_requests", 100),
        window_seconds=config.get("window_seconds", 3600),
        policy_id=config.get("policy_id", "rate-limit-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_time_based(config: dict[str, Any]) -> TimeBasedPolicy:
    days = config.get("allowed_days")
    return TimeBasedPolicy(
        allowed_days=(frozenset(days) if days is not None else frozenset(range(7))),
        allowed_start_time=time(config.get("start_hour", 0), 0),
        allowed_end_time=time(config.get("end_hour", 23), 59, 59),
        utc_offset_hours=config.get("utc_offset_hours", 0),
        policy_id=config.get("policy_id", "time-based-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_abac(config: dict[str, Any]) -> AttributeBasedPolicy:
    """Build ABAC from declarative metadata checks.

    Supports a ``metadata_conditions`` dict mapping metadata keys to
    expected values::

        type: abac
        require_all: true
        metadata_conditions:
          department: engineering
          clearance_level: 3

    Each condition becomes a rule checking
    ``context.metadata.get(key) == value``.
    """
    conditions = config.get("metadata_conditions", {})
    rules: dict[str, Any] = {}
    for key, expected in conditions.items():

        def _make_check(k: str, v: Any):
            def check(ctx: Any) -> bool:
                return ctx.metadata.get(k) == v

            return check

        rules[f"check_{key}"] = _make_check(key, expected)

    return AttributeBasedPolicy(
        rules=rules,
        require_all=config.get("require_all", True),
        policy_id=config.get("policy_id", "abac-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_resource_scoped(config: dict[str, Any]) -> ResourceScopedPolicy:
    """Build resource-scoped from nested policy definitions.

    Example::

        type: resource_scoped
        prefix_match: true
        resource_rules:
          "tool:admin":
            type: rbac
            role_mappings:
              admin: ["*"]
          "tool:public":
            type: allow_all
        default:
          type: deny_all
    """
    resource_rules: dict[str, PolicyProvider] = {}
    for resource_id, sub_config in config.get("resource_rules", {}).items():
        resource_rules[resource_id] = _build_single_policy(sub_config)

    default_config = config.get("default")
    default_policy = _build_single_policy(default_config) if default_config else None

    return ResourceScopedPolicy(
        resource_rules=resource_rules,
        default_policy=default_policy,
        prefix_match=config.get("prefix_match", False),
        policy_id=config.get("policy_id", "resource-scoped-policy"),
        version=config.get("version", "1.0.0"),
    )


def _build_allow_all(config: dict[str, Any]) -> AllowAllPolicy:
    return AllowAllPolicy()


def _build_deny_all(config: dict[str, Any]) -> DenyAllPolicy:
    return DenyAllPolicy()


# Map of type names → factory functions.
_POLICY_FACTORIES: dict[str, Any] = {
    "allowlist": _build_allowlist,
    "denylist": _build_denylist,
    "rbac": _build_rbac,
    "role_based": _build_rbac,
    "rate_limit": _build_rate_limit,
    "time_based": _build_time_based,
    "temporal": _build_time_based,
    "abac": _build_abac,
    "attribute_based": _build_abac,
    "resource_scoped": _build_resource_scoped,
    "allow_all": _build_allow_all,
    "deny_all": _build_deny_all,
}


# ── Composition builders ──────────────────────────────────────────


_COMPOSITION_MAP = {
    "all_of": "all_of",
    "allof": "all_of",
    "all": "all_of",
    "any_of": "any_of",
    "anyof": "any_of",
    "any": "any_of",
    "first_match": "first_match",
    "firstmatch": "first_match",
    "not": "not",
}


# ── Helpers ───────────────────────────────────────────────────────


def _parse_decision(value: str) -> PolicyDecision:
    """Parse a string into a PolicyDecision enum."""
    return PolicyDecision(value.lower())


def _build_single_policy(config: dict[str, Any]) -> PolicyProvider:
    """Build a single PolicyProvider from a config dict.

    Handles both simple policies and nested compositions via
    ``composition`` key.
    """
    # If it has a "composition" or "policies" key, it's a composite
    if "composition" in config or "policies" in config:
        return _build_composite(config)

    policy_type = config.get("type", "")
    factory = _POLICY_FACTORIES.get(policy_type)
    if factory is None:
        raise ValueError(
            f"Unknown policy type: {policy_type!r}. "
            f"Available types: {sorted(_POLICY_FACTORIES.keys())}"
        )
    return factory(config)


def _build_composite(config: dict[str, Any]) -> PolicyProvider:
    """Build a composite policy from a config with ``policies`` list."""
    raw_composition = config.get("composition", "all_of")
    composition = _COMPOSITION_MAP.get(raw_composition.lower())
    if composition is None:
        raise ValueError(
            f"Unknown composition: {raw_composition!r}. "
            f"Available: {sorted(_COMPOSITION_MAP.keys())}"
        )

    policy_id = config.get("policy_id", f"declarative-{composition}")
    version = config.get("version", "1.0.0")
    children_config = config.get("policies", [])

    children: list[PolicyProvider] = [
        _build_single_policy(child) for child in children_config
    ]

    if composition == "not":
        if len(children) != 1:
            raise ValueError("'not' composition requires exactly one child policy")
        return Not(children[0], policy_id=policy_id, version=version)

    if composition == "all_of":
        return AllOf(*children, policy_id=policy_id, version=version)

    if composition == "any_of":
        return AnyOf(
            *children,
            policy_id=policy_id,
            version=version,
            require_minimum=config.get("require_minimum", 1),
        )

    # first_match
    default = _parse_decision(config.get("default_decision", "deny"))
    return FirstMatch(
        *children,
        policy_id=policy_id,
        version=version,
        default_decision=default,
    )


# ── Public API ────────────────────────────────────────────────────


def load_policy(source: str | Path | dict[str, Any]) -> PolicyProvider:
    """Load a policy from a YAML/JSON file or a dict.

    Args:
        source: Path to a YAML/JSON file, or a dict with the policy
            definition.

    Returns:
        A fully-built PolicyProvider ready for use with PolicyEngine.

    Raises:
        ValueError: If the config is malformed or references unknown types.
        FileNotFoundError: If the file path doesn't exist.
        ImportError: If YAML loading is requested but PyYAML is not installed.

    Example::

        policy = load_policy("policies/production.yaml")
        engine = PolicyEngine(providers=[policy])
    """
    if isinstance(source, dict):
        return _build_single_policy(source)

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    text = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for loading YAML policy files. "
                "Install it with: pip install pyyaml"
            )
        config = yaml.safe_load(text)
    elif path.suffix == ".json":
        config = json.loads(text)
    else:
        raise ValueError(
            f"Unsupported file format: {path.suffix!r}. Use .yaml, .yml, or .json"
        )

    if not isinstance(config, dict):
        raise ValueError(
            f"Policy file must contain a mapping, got {type(config).__name__}"
        )

    return _build_single_policy(config)


def dump_policy_schema() -> dict[str, Any]:
    """Return a JSON-serializable schema describing the declarative format.

    Useful for documentation, IDE completion hints, or validation.
    """
    return {
        "description": "Declarative policy definition schema for SecureMCP",
        "policy_types": {
            "allowlist": {
                "description": "Allow only listed resources (glob patterns supported)",
                "fields": {
                    "allowed": "list[str] — resource IDs or glob patterns",
                },
            },
            "denylist": {
                "description": "Block listed resources (glob patterns supported)",
                "fields": {
                    "denied": "list[str] — resource IDs or glob patterns",
                },
            },
            "rbac": {
                "description": "Role-based access control",
                "aliases": ["role_based"],
                "fields": {
                    "role_mappings": "dict[str, list[str]] — role → allowed actions",
                    "default_decision": "str — 'allow' | 'deny' | 'defer' (default: deny)",
                },
            },
            "rate_limit": {
                "description": "Sliding-window rate limiting per actor",
                "fields": {
                    "max_requests": "int — max requests per window (default: 100)",
                    "window_seconds": "int — window duration in seconds (default: 3600)",
                },
            },
            "time_based": {
                "description": "Time-of-day and day-of-week restrictions",
                "aliases": ["temporal"],
                "fields": {
                    "allowed_days": "list[int] — 0=Mon..6=Sun (default: all)",
                    "start_hour": "int — 0-23 (default: 0)",
                    "end_hour": "int — 0-23 (default: 23)",
                    "utc_offset_hours": "int (default: 0)",
                },
            },
            "abac": {
                "description": "Attribute-based policy using metadata checks",
                "aliases": ["attribute_based"],
                "fields": {
                    "metadata_conditions": "dict[str, Any] — key/value checks against context.metadata",
                    "require_all": "bool — True=AND, False=OR (default: True)",
                },
            },
            "resource_scoped": {
                "description": "Delegate to sub-policies per resource ID",
                "fields": {
                    "resource_rules": "dict[str, policy_config] — resource → sub-policy",
                    "default": "policy_config — fallback policy",
                    "prefix_match": "bool (default: False)",
                },
            },
            "allow_all": {"description": "Always allow", "fields": {}},
            "deny_all": {"description": "Always deny", "fields": {}},
        },
        "compositions": {
            "all_of": {
                "description": "All child policies must ALLOW",
                "aliases": ["allof", "all"],
            },
            "any_of": {
                "description": "At least N child policies must ALLOW",
                "aliases": ["anyof", "any"],
                "extra_fields": {"require_minimum": "int (default: 1)"},
            },
            "first_match": {
                "description": "First non-DEFER result wins",
                "aliases": ["firstmatch"],
                "extra_fields": {"default_decision": "str (default: 'deny')"},
            },
            "not": {
                "description": "Invert child decision (exactly 1 child)",
                "aliases": [],
            },
        },
        "common_fields": {
            "type": "str — policy type name (required for leaf policies)",
            "policy_id": "str — custom identifier",
            "version": "str — version string",
            "composition": "str — composition type (for composite policies)",
            "policies": "list[policy_config] — child policies (for composite)",
        },
    }
