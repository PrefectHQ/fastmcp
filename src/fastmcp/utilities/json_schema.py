from __future__ import annotations

import copy
from collections import defaultdict


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


def _prune_unused_defs(schema: dict) -> dict:
    """Walk the schema and prune unused defs."""

    root_defs: set[str] = set()
    referenced_by: defaultdict[str, list] = defaultdict(list)

    defs = schema.get("$defs")
    if defs is None:
        return schema

    def walk(
        node: object, current_def: str | None = None, skip_defs: bool = False
    ) -> None:
        if isinstance(node, dict):
            # Process $ref for definition tracking
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                if current_def:
                    referenced_by[def_name].append(current_def)
                else:
                    root_defs.add(def_name)

            # Walk children
            for k, v in node.items():
                if skip_defs and k == "$defs":
                    continue

                walk(v, current_def=current_def)

        elif isinstance(node, list):
            for v in node:
                walk(v)

    # Traverse the schema once, skipping the $defs
    walk(schema, skip_defs=True)

    # Now figure out what defs reference other defs
    for def_name, value in defs.items():
        walk(value, current_def=def_name)

    # Figure out what defs were referenced directly or recursively
    def def_is_referenced(def_name):
        if def_name in root_defs:
            return True
        references = referenced_by.get(def_name)
        if references:
            for reference in references:
                if def_is_referenced(reference):
                    return True
        return False

    # Remove orphaned definitions if requested
    for def_name in list(defs):
        if not def_is_referenced(def_name):
            defs.pop(def_name)
    if not defs:
        schema.pop("$defs", None)

    return schema


def _walk_and_prune(
    schema: dict,
    prune_titles: bool = False,
    prune_additional_properties: bool = False,
) -> dict:
    """Walk the schema and optionally prune titles and additionalProperties: false."""

    def walk(node: object) -> None:
        if isinstance(node, dict):
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

    walk(schema)

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
    if prune_titles or prune_additional_properties:
        schema = _walk_and_prune(
            schema,
            prune_titles=prune_titles,
            prune_additional_properties=prune_additional_properties,
        )
    if prune_defs:
        schema = _prune_unused_defs(schema)

    return schema
