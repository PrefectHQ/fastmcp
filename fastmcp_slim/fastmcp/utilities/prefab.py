"""Lazy helpers for FastMCP's optional Prefab UI integration."""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib.util import find_spec
from typing import Any


@lru_cache(maxsize=1)
def prefab_available() -> bool:
    """Return whether Prefab UI is installed without importing it."""
    return find_spec("prefab_ui") is not None


@lru_cache(maxsize=1)
def _get_prefab_types() -> tuple[type[Any], type[Any]] | None:
    """Import and return Prefab's public app and component types on demand."""
    if not prefab_available():
        return None

    from prefab_ui.app import PrefabApp
    from prefab_ui.components.base import Component

    return PrefabApp, Component


def _could_be_prefab(value_or_type: Any) -> bool:
    """Cheaply reject ordinary values before importing Prefab UI."""
    candidate_type = (
        value_or_type if isinstance(value_or_type, type) else type(value_or_type)
    )
    module = getattr(candidate_type, "__module__", "")
    return (
        "prefab_ui" in sys.modules
        or module == "prefab_ui"
        or module.startswith("prefab_ui.")
    )


def is_prefab_type(candidate: Any) -> bool:
    """Return whether a type is a Prefab app or component type."""
    if not isinstance(candidate, type) or not _could_be_prefab(candidate):
        return False

    prefab_types = _get_prefab_types()
    return prefab_types is not None and issubclass(candidate, prefab_types)


def is_prefab_app(value: Any) -> bool:
    """Return whether a value is a Prefab app."""
    if not _could_be_prefab(value):
        return False

    prefab_types = _get_prefab_types()
    return prefab_types is not None and isinstance(value, prefab_types[0])


def is_prefab_component(value: Any) -> bool:
    """Return whether a value is a Prefab component."""
    if not _could_be_prefab(value):
        return False

    prefab_types = _get_prefab_types()
    return prefab_types is not None and isinstance(value, prefab_types[1])


def prefab_app_from_component(component: Any) -> Any:
    """Wrap a Prefab component in a Prefab app."""
    prefab_types = _get_prefab_types()
    if prefab_types is None or not isinstance(component, prefab_types[1]):
        raise TypeError("Expected a Prefab UI component")
    return prefab_types[0](view=component)
