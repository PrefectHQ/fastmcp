"""Policy serialization helpers for management, versioning, and rollback.

This module provides a JSON-safe representation of live policy providers.
The same config shape is used for:

- Management APIs that create or edit providers
- Version snapshots stored by PolicyVersionManager
- Restoring the live provider chain after rollback or restart

The preferred representation is a declarative config for built-in policies.
For custom providers, the module falls back to a lightweight Python-class
snapshot when the object's state is JSON-safe.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any, cast

from fastmcp.server.security.policy.composition import AllOf, AnyOf, FirstMatch, Not
from fastmcp.server.security.policy.declarative import load_policy
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
    PolicyProvider,
)

_SNAPSHOT_FORMAT = "securemcp-policy-set/v1"
_PYTHON_CLASS_TYPE = "python_class"


def describe_policy_provider(provider: PolicyProvider, *, index: int) -> dict[str, Any]:
    """Return a UI-friendly description of a live provider."""

    provider_type = type(provider).__name__
    try:
        config = policy_provider_to_config(provider)
        editable = True
        summary = describe_policy_config(config)
        policy_id = _config_policy_id(config)
        policy_version = _config_policy_version(config)
    except TypeError as exc:
        config = {
            "type": _PYTHON_CLASS_TYPE,
            "class_path": _class_path(type(provider)),
            "error": str(exc),
        }
        editable = False
        summary = (
            "This provider is active, but its runtime state cannot be edited "
            "through the management API."
        )
        policy_id = getattr(provider, "policy_id", None) or getattr(
            provider, "_id", None
        )
        policy_version = getattr(provider, "version", None) or getattr(
            provider, "_version", None
        )

    return {
        "index": index,
        "type": provider_type,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "editable": editable,
        "summary": summary,
        "config": config,
    }


def describe_policy_config(config: dict[str, Any]) -> str:
    """Return a human-readable summary for a policy config."""

    policy_type = str(config.get("type") or "")
    composition = str(config.get("composition") or "")

    if composition == "all_of":
        children = list(config.get("policies") or [])
        return f"All {len(children)} child policies must allow."
    if composition == "any_of":
        children = list(config.get("policies") or [])
        minimum = int(config.get("require_minimum", 1))
        return f"At least {minimum} of {len(children)} child policies must allow."
    if composition == "first_match":
        children = list(config.get("policies") or [])
        return (
            f"The first matching decision wins across {len(children)} child policies."
        )
    if composition == "not":
        return "Inverts the result of a single child policy."

    if policy_type == "allowlist":
        allowed = list(config.get("allowed") or [])
        return f"Allows {len(allowed)} listed resource patterns."
    if policy_type == "denylist":
        denied = list(config.get("denied") or [])
        return f"Blocks {len(denied)} listed resource patterns."
    if policy_type in {"rbac", "role_based"}:
        roles = dict(config.get("role_mappings") or {})
        return f"Maps {len(roles)} roles to allowed actions."
    if policy_type == "rate_limit":
        return (
            f"Limits each actor to {config.get('max_requests', 100)} requests "
            f"per {config.get('window_seconds', 3600)} seconds."
        )
    if policy_type in {"time_based", "temporal"}:
        days = list(config.get("allowed_days") or [])
        if days:
            return f"Allows access only on {len(days)} configured days."
        return "Allows access only during the configured time window."
    if policy_type == "resource_scoped":
        rules = dict(config.get("resource_rules") or {})
        return f"Delegates access rules across {len(rules)} resource-specific entries."
    if policy_type == "abac":
        conditions = dict(config.get("metadata_conditions") or {})
        return f"Checks {len(conditions)} metadata-based access conditions."
    if policy_type == "allow_all":
        return "Allows every request."
    if policy_type == "deny_all":
        return "Denies every request."
    if policy_type == _PYTHON_CLASS_TYPE:
        return "Custom Python policy provider."

    return "Managed policy provider."


def policy_snapshot(
    providers: list[PolicyProvider], *, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a JSON-safe snapshot of the active provider set."""

    return {
        "format": _SNAPSHOT_FORMAT,
        "providers": [policy_provider_to_config(provider) for provider in providers],
        "metadata": dict(metadata or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def providers_from_snapshot(snapshot: dict[str, Any]) -> list[PolicyProvider]:
    """Restore a provider list from a stored snapshot."""

    if snapshot.get("format") != _SNAPSHOT_FORMAT:
        raise ValueError("Unsupported policy snapshot format.")

    raw_providers = snapshot.get("providers")
    if not isinstance(raw_providers, list):
        raise ValueError("Policy snapshot is missing a provider list.")

    providers: list[PolicyProvider] = []
    for raw_provider in raw_providers:
        if not isinstance(raw_provider, dict):
            raise ValueError("Each provider snapshot must be an object.")
        providers.append(policy_provider_from_config(raw_provider))
    return providers


def policy_provider_to_config(provider: PolicyProvider) -> dict[str, Any]:
    """Serialize a provider to a management-friendly config dict."""

    if isinstance(provider, AllowAllPolicy):
        return {"type": "allow_all"}

    if isinstance(provider, DenyAllPolicy):
        return {"type": "deny_all"}

    if isinstance(provider, AllowlistPolicy):
        return {
            "type": "allowlist",
            "allowed": sorted(provider.allowed),
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    if isinstance(provider, DenylistPolicy):
        return {
            "type": "denylist",
            "denied": sorted(provider.denied),
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    if isinstance(provider, RoleBasedPolicy) and provider.role_resolver is None:
        return {
            "type": "rbac",
            "role_mappings": {
                role: sorted(actions)
                for role, actions in sorted(provider.role_mappings.items())
            },
            "policy_id": provider.policy_id,
            "version": provider.version,
            "default_decision": provider.default_decision.value,
        }

    if isinstance(provider, RateLimitPolicy):
        return {
            "type": "rate_limit",
            "max_requests": provider.max_requests,
            "window_seconds": provider.window_seconds,
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    if isinstance(provider, TimeBasedPolicy):
        if (
            provider.allowed_start_time.minute == 0
            and provider.allowed_start_time.second == 0
            and provider.allowed_end_time.minute in {0, 59}
            and provider.allowed_end_time.second in {0, 59}
        ):
            return {
                "type": "time_based",
                "allowed_days": sorted(provider.allowed_days),
                "start_hour": provider.allowed_start_time.hour,
                "end_hour": provider.allowed_end_time.hour,
                "utc_offset_hours": provider.utc_offset_hours,
                "policy_id": provider.policy_id,
                "version": provider.version,
            }
        return _python_class_config(provider)

    if isinstance(provider, AttributeBasedPolicy):
        metadata_conditions = getattr(provider, "_metadata_conditions", None)
        if isinstance(metadata_conditions, dict):
            return {
                "type": "abac",
                "metadata_conditions": dict(metadata_conditions),
                "require_all": provider.require_all,
                "policy_id": provider.policy_id,
                "version": provider.version,
            }
        return _python_class_config(provider)

    if isinstance(provider, ResourceScopedPolicy):
        return {
            "type": "resource_scoped",
            "resource_rules": {
                resource_id: policy_provider_to_config(child)
                for resource_id, child in provider.resource_rules.items()
            },
            "default": (
                policy_provider_to_config(provider.default_policy)
                if provider.default_policy is not None
                else None
            ),
            "prefix_match": provider.prefix_match,
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    if isinstance(provider, AllOf):
        return {
            "composition": "all_of",
            "policies": [
                policy_provider_to_config(child) for child in provider.policies
            ],
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    if isinstance(provider, AnyOf):
        return {
            "composition": "any_of",
            "policies": [
                policy_provider_to_config(child) for child in provider.policies
            ],
            "policy_id": provider.policy_id,
            "version": provider.version,
            "require_minimum": provider.require_minimum,
        }

    if isinstance(provider, FirstMatch):
        return {
            "composition": "first_match",
            "policies": [
                policy_provider_to_config(child) for child in provider.policies
            ],
            "policy_id": provider.policy_id,
            "version": provider.version,
            "default_decision": provider.default_decision.value,
        }

    if isinstance(provider, Not):
        if provider.policy is None:
            raise TypeError("Not policy is missing its child provider.")
        return {
            "composition": "not",
            "policies": [policy_provider_to_config(provider.policy)],
            "policy_id": provider.policy_id,
            "version": provider.version,
        }

    return _python_class_config(provider)


def policy_provider_from_config(config: dict[str, Any]) -> PolicyProvider:
    """Build a provider from a management config dict."""

    if str(config.get("type") or "") == _PYTHON_CLASS_TYPE:
        return _python_class_provider(config)

    return load_policy(config)


def _python_class_config(provider: PolicyProvider) -> dict[str, Any]:
    state = _provider_state(provider)
    return {
        "type": _PYTHON_CLASS_TYPE,
        "class_path": _class_path(type(provider)),
        "state": _encode_value(state),
    }


def _python_class_provider(config: dict[str, Any]) -> PolicyProvider:
    class_path = str(config.get("class_path") or "").strip()
    if not class_path:
        raise ValueError("Custom provider config requires a `class_path` value.")

    provider_cls = _import_class(class_path)
    raw_state = config.get("state", {})
    decoded_state = _decode_value(raw_state)
    if not isinstance(decoded_state, dict):
        raise ValueError("Custom provider state must decode to an object.")

    if is_dataclass(provider_cls):
        init_kwargs: dict[str, Any] = {}
        post_init_values: dict[str, Any] = {}
        for field_def in fields(provider_cls):
            if field_def.name not in decoded_state:
                continue
            if field_def.init:
                init_kwargs[field_def.name] = decoded_state[field_def.name]
            else:
                post_init_values[field_def.name] = decoded_state[field_def.name]
        instance = provider_cls(**init_kwargs)
        for key, value in post_init_values.items():
            setattr(instance, key, value)
        return cast(PolicyProvider, instance)

    try:
        instance = provider_cls(**decoded_state)
        return cast(PolicyProvider, instance)
    except TypeError:
        instance = provider_cls.__new__(provider_cls)
        if hasattr(instance, "__dict__"):
            instance.__dict__.update(decoded_state)
            return cast(PolicyProvider, instance)
        raise TypeError(
            f"Could not restore policy provider {class_path!r} from saved state."
        ) from None


def _provider_state(provider: PolicyProvider) -> dict[str, Any]:
    if is_dataclass(provider):
        return {
            field_def.name: getattr(provider, field_def.name)
            for field_def in fields(provider)
            if not field_def.name.startswith("_")
        }

    if hasattr(provider, "__dict__"):
        return {
            key: value
            for key, value in provider.__dict__.items()
            if not key.startswith("__")
        }

    return {}


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, datetime):
        return {"__policy_value__": "datetime", "value": value.isoformat()}

    if isinstance(value, time):
        return {"__policy_value__": "time", "value": value.isoformat()}

    if isinstance(value, Enum):
        return {
            "__policy_value__": "enum",
            "class_path": _class_path(type(value)),
            "value": value.value,
        }

    if isinstance(value, frozenset):
        return {
            "__policy_value__": "frozenset",
            "items": [_encode_value(item) for item in sorted(value, key=str)],
        }

    if isinstance(value, set):
        return {
            "__policy_value__": "set",
            "items": [_encode_value(item) for item in sorted(value, key=str)],
        }

    if isinstance(value, tuple):
        return {
            "__policy_value__": "tuple",
            "items": [_encode_value(item) for item in value],
        }

    if isinstance(value, list):
        return [_encode_value(item) for item in value]

    if isinstance(value, defaultdict):
        return {
            "__policy_value__": "dict",
            "items": {str(key): _encode_value(item) for key, item in value.items()},
        }

    if isinstance(value, dict):
        return {str(key): _encode_value(item) for key, item in value.items()}

    if _looks_like_policy_provider(value):
        return {
            "__policy_value__": "policy_provider",
            "config": policy_provider_to_config(cast(PolicyProvider, value)),
        }

    raise TypeError(
        f"Unsupported policy value for serialization: {type(value).__name__}"
    )


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]

    if isinstance(value, dict):
        marker = value.get("__policy_value__")
        if marker == "datetime":
            return datetime.fromisoformat(str(value["value"]))
        if marker == "time":
            return time.fromisoformat(str(value["value"]))
        if marker == "enum":
            enum_cls = _import_class(str(value["class_path"]))
            return enum_cls(value["value"])
        if marker == "frozenset":
            return frozenset(
                _decode_value(item) for item in list(value.get("items", []))
            )
        if marker == "set":
            return {_decode_value(item) for item in list(value.get("items", []))}
        if marker == "tuple":
            return tuple(_decode_value(item) for item in list(value.get("items", [])))
        if marker == "dict":
            items = value.get("items", {})
            if not isinstance(items, dict):
                raise ValueError("Encoded policy dict must store an object in `items`.")
            return {key: _decode_value(item) for key, item in items.items()}
        if marker == "policy_provider":
            raw_config = value.get("config")
            if not isinstance(raw_config, dict):
                raise ValueError(
                    "Encoded policy provider must include a config object."
                )
            return policy_provider_from_config(raw_config)
        return {key: _decode_value(item) for key, item in value.items()}

    return value


def _config_policy_id(config: dict[str, Any]) -> str | None:
    if "policy_id" in config:
        return str(config["policy_id"])
    policy_type = str(config.get("type") or "")
    if policy_type == "allow_all":
        return "allow-all"
    if policy_type == "deny_all":
        return "deny-all"
    return None


def _config_policy_version(config: dict[str, Any]) -> str | None:
    if "version" in config:
        return str(config["version"])
    policy_type = str(config.get("type") or "")
    if policy_type in {"allow_all", "deny_all"}:
        return "1.0.0"
    return None


def _looks_like_policy_provider(value: Any) -> bool:
    return all(
        hasattr(value, attr)
        for attr in ("evaluate", "get_policy_id", "get_policy_version")
    )


def _class_path(cls: type[Any]) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def _import_class(class_path: str) -> type[Any]:
    module_name, sep, qualname = class_path.partition(":")
    if not sep or not module_name or not qualname:
        raise ValueError(f"Invalid class path: {class_path!r}")

    module = importlib.import_module(module_name)
    target: Any = module
    for part in qualname.split("."):
        target = getattr(target, part)
    if not isinstance(target, type):
        raise TypeError(f"Resolved object is not a class: {class_path!r}")
    return target
