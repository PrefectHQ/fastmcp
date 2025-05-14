from __future__ import annotations

import copy


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

    return schema


def _walk_and_prune(
    schema: dict,
    prune_defs: bool = False,
    prune_titles: bool = False,
    prune_additional_properties: bool = False,
) -> dict:
    """Walk the schema and optionally prune titles, unused definitions, and additionalProperties: false."""

    # Will only be used if prune_defs is True
    used_defs: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            # Process $ref for definition tracking
            if prune_defs:
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    used_defs.add(ref.split("/")[-1])

            # Remove title if requested
            if prune_titles and "title" in node:
                node.pop("title")

            # Remove additionalProperties: false at any level if requested
            if (
                prune_additional_properties
                and node.get("additionalProperties", None) is False
            ):
                node.pop("additionalProperties")

            # Walk children
            for v in node.values():
                walk(v)

        elif isinstance(node, list):
            for v in node:
                walk(v)

    # Traverse the schema once
    walk(schema)

    # Remove orphaned definitions if requested
    if prune_defs:
        defs = schema.get("$defs", {})
        for def_name in list(defs):
            if def_name not in used_defs:
                defs.pop(def_name)
        if not defs:
            schema.pop("$defs", None)

    return schema


def _prune_additional_properties(schema: dict) -> dict:
    """Remove additionalProperties from the schema if it is False."""
    if schema.get("additionalProperties", None) is False:
        schema.pop("additionalProperties")
    return schema


def compress_schema(
    schema: dict,
    prune_params: list[str] | None = None,
    prune_defs: bool = True,
    prune_additional_properties: bool = True,
    prune_titles: bool = False,
) -> dict:
    """
    Remove the given parameters from the schema.

    Args:
        schema: The schema to compress
        prune_params: List of parameter names to remove from properties
        prune_defs: Whether to remove unused definitions
        prune_additional_properties: Whether to remove additionalProperties: false
        prune_titles: Whether to remove title fields from the schema
    """
    # Make a copy so we don't modify the original
    schema = copy.deepcopy(schema)

    # Remove specific parameters if requested
    for param in prune_params or []:
        schema = _prune_param(schema, param=param)

    # Do a single walk to handle pruning operations
    if prune_defs or prune_titles or prune_additional_properties:
        schema = _walk_and_prune(
            schema,
            prune_defs=prune_defs,
            prune_titles=prune_titles,
            prune_additional_properties=prune_additional_properties,
        )

    return schema
