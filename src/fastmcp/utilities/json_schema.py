from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence


def _prune_param(schema: dict, param: str) -> dict:
    """Return a new schema with *param* removed from `properties`, `required`,
    and (if no longer referenced) `$defs`.
    """

    # ── 1. drop from properties/required ──────────────────────────────
    props = schema.get("properties", {})
    removed = props.pop(param, None)
    if removed is None:  # nothing to do
        return schema
    # Keep empty properties object rather than removing it entirely
    schema["properties"] = props
    if param in schema.get("required", []):
        schema["required"].remove(param)
        if not schema["required"]:
            schema.pop("required")

    # ── 2. collect all remaining local $ref targets ───────────────────
    used_defs: set[str] = set()

    def walk(node: object) -> None:  # depth-first traversal
        if isinstance(node, Mapping):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                used_defs.add(ref.split("/")[-1])
            for v in node.values():
                walk(v)
        elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
            for v in node:
                walk(v)

    walk(schema)

    # ── 3. remove orphaned definitions ────────────────────────────────
    defs = schema.get("$defs", {})
    for def_name in list(defs):
        if def_name not in used_defs:
            defs.pop(def_name)
    if not defs:
        schema.pop("$defs", None)

    return schema


def prune_params(schema: dict, params: list[str]) -> dict:
    """
    Remove the given parameters from the schema.

    """
    schema = copy.deepcopy(schema)
    for param in params:
        schema = _prune_param(schema, param=param)
    return schema
