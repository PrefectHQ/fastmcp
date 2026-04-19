"""Generic helpers for collection types (dicts, lists, etc.)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `update` into `base` in place and return it.

    Dict values are merged recursively; other values (including `None`
    and primitives) overwrite. Lists are not concatenated — `update`'s
    list replaces `base`'s list.

    Values copied from `update` are deep-copied at assignment time so
    that subsequent merges into `base` never mutate data owned by the
    caller (e.g. a plugin returning a class-level dict from a hook).
    """
    for key, value in update.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            deep_merge(existing, value)
        else:
            base[key] = deepcopy(value)
    return base
