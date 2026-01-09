from __future__ import annotations

from collections import defaultdict
from typing import Any

from jsonref import JsonRefError, replace_refs


def dereference_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve all $ref references in a JSON schema by inlining definitions.

    This function resolves $ref references that point to $defs, replacing them
    with the actual definition content while preserving sibling keywords (like
    description, default, examples) that Pydantic places alongside $ref.

    This is necessary because some MCP clients (e.g., VS Code Copilot) don't
    properly handle $ref in tool input schemas.

    For self-referencing/circular schemas where full dereferencing is not possible,
    this function falls back to resolving only the root-level $ref while preserving
    $defs for nested references.

    Args:
        schema: JSON schema dict that may contain $ref references

    Returns:
        A new schema dict with $ref resolved where possible and $defs removed
        when no longer needed

    Example:
        >>> schema = {
        ...     "$defs": {"Category": {"enum": ["a", "b"], "type": "string"}},
        ...     "properties": {"cat": {"$ref": "#/$defs/Category", "default": "a"}}
        ... }
        >>> resolved = dereference_refs(schema)
        >>> # Result: {"properties": {"cat": {"enum": ["a", "b"], "type": "string", "default": "a"}}}
    """
    try:
        # Use jsonref to resolve all $ref references
        # proxies=False returns plain dicts (not proxy objects)
        # lazy_load=False resolves immediately
        dereferenced = replace_refs(schema, proxies=False, lazy_load=False)

        # Merge sibling keywords that were lost during dereferencing
        # Pydantic puts description, default, examples as siblings to $ref
        defs = schema.get("$defs", {})
        merged = _merge_ref_siblings(schema, dereferenced, defs)
        # Type assertion: top-level schema is always a dict
        assert isinstance(merged, dict)
        dereferenced = merged

        # Remove $defs since all references have been resolved
        if "$defs" in dereferenced:
            dereferenced = {k: v for k, v in dereferenced.items() if k != "$defs"}

        return dereferenced

    except JsonRefError:
        # Self-referencing/circular schemas can't be fully dereferenced
        # Fall back to resolving only root-level $ref (for MCP spec compliance)
        return resolve_root_ref(schema)


def _merge_ref_siblings(
    original: Any,
    dereferenced: Any,
    defs: dict[str, Any],
    visited: set[str] | None = None,
) -> Any:
    """Merge sibling keywords from original $ref nodes into dereferenced schema.

    When jsonref resolves $ref, it replaces the entire node with the referenced
    definition, losing any sibling keywords like description, default, or examples.
    This function walks both trees in parallel and merges those siblings back.

    Args:
        original: The original schema with $ref and potential siblings
        dereferenced: The schema after jsonref processing
        defs: The $defs from the original schema, for looking up referenced definitions
        visited: Set of definition names already being processed (prevents cycles)

    Returns:
        The dereferenced schema with sibling keywords restored
    """
    if visited is None:
        visited = set()

    if isinstance(original, dict) and isinstance(dereferenced, dict):
        # Check if original had a $ref
        if "$ref" in original:
            ref = original["$ref"]
            siblings = {k: v for k, v in original.items() if k not in ("$ref", "$defs")}

            # Look up the referenced definition to process its nested siblings
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                # Prevent infinite recursion on circular references
                if def_name in defs and def_name not in visited:
                    # Recursively process the definition's content for nested siblings
                    dereferenced = _merge_ref_siblings(
                        defs[def_name], dereferenced, defs, visited | {def_name}
                    )

            if siblings:
                # Merge local siblings, which take precedence
                merged = dict(dereferenced)
                merged.update(siblings)
                return merged
            return dereferenced

        # Recurse into nested structures
        result = {}
        for key, value in dereferenced.items():
            if key in original:
                result[key] = _merge_ref_siblings(original[key], value, defs, visited)
            else:
                result[key] = value
        return result

    elif isinstance(original, list) and isinstance(dereferenced, list):
        # Process list items in parallel
        min_len = min(len(original), len(dereferenced))
        return [
            _merge_ref_siblings(o, d, defs, visited)
            for o, d in zip(original[:min_len], dereferenced[:min_len], strict=False)
        ] + dereferenced[min_len:]

    return dereferenced


def resolve_root_ref(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref at root level to meet MCP spec requirements.

    MCP specification requires outputSchema to have "type": "object" at the root level.
    When Pydantic generates schemas for self-referential models, it uses $ref at the
    root level pointing to $defs. This function resolves such references by inlining
    the referenced definition while preserving $defs for nested references.

    Args:
        schema: JSON schema dict that may have $ref at root level

    Returns:
        A new schema dict with root-level $ref resolved, or the original schema
        if no resolution is needed

    Example:
        >>> schema = {
        ...     "$defs": {"Node": {"type": "object", "properties": {...}}},
        ...     "$ref": "#/$defs/Node"
        ... }
        >>> resolved = resolve_root_ref(schema)
        >>> # Result: {"type": "object", "properties": {...}, "$defs": {...}}
    """
    # Only resolve if we have $ref at root level with $defs but no explicit type
    if "$ref" in schema and "$defs" in schema and "type" not in schema:
        ref = schema["$ref"]
        # Only handle local $defs references
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            def_name = ref.split("/")[-1]
            defs = schema["$defs"]
            if def_name in defs:
                # Create a new schema by copying the referenced definition
                resolved = dict(defs[def_name])
                # Preserve $defs for nested references (other fields may still use them)
                resolved["$defs"] = defs
                return resolved
    return schema


def _prune_param(schema: dict[str, Any], param: str) -> dict[str, Any]:
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


def _single_pass_optimize(
    schema: dict[str, Any],
    prune_titles: bool = False,
    prune_additional_properties: bool = False,
    prune_defs: bool = True,
) -> dict[str, Any]:
    """
    Optimize JSON schemas in a single traversal for better performance.

    This function combines three schema cleanup operations that would normally require
    separate tree traversals:

    1. **Remove unused definitions** (prune_defs): Finds and removes `$defs` entries
       that aren't referenced anywhere in the schema, reducing schema size.

    2. **Remove titles** (prune_titles): Strips `title` fields throughout the schema
       to reduce verbosity while preserving functional information.

    3. **Remove restrictive additionalProperties** (prune_additional_properties):
       Removes `"additionalProperties": false` constraints to make schemas more flexible.

    **Performance Benefits:**
    - Single tree traversal instead of multiple passes (2-3x faster)
    - Immutable design prevents shared reference bugs
    - Early termination prevents runaway recursion on deeply nested schemas

    **Algorithm Overview:**
    1. Traverse main schema, collecting $ref references and applying cleanups
    2. Traverse $defs section to map inter-definition dependencies
    3. Remove unused definitions based on reference analysis

    Args:
        schema: JSON schema dict to optimize (not modified)
        prune_titles: Remove title fields for cleaner output
        prune_additional_properties: Remove "additionalProperties": false constraints
        prune_defs: Remove unused $defs entries to reduce size

    Returns:
        A new optimized schema dict

    Example:
        >>> schema = {
        ...     "type": "object",
        ...     "title": "MySchema",
        ...     "additionalProperties": False,
        ...     "$defs": {"UnusedDef": {"type": "string"}}
        ... }
        >>> result = _single_pass_optimize(schema, prune_titles=True, prune_defs=True)
        >>> # Result: {"type": "object", "additionalProperties": False}
    """
    if not (prune_defs or prune_titles or prune_additional_properties):
        return schema  # Nothing to do

    # Phase 1: Collect references and apply simple cleanups
    # Track which $defs are referenced from the main schema and from other $defs
    root_refs: set[str] = set()  # $defs referenced directly from main schema
    def_dependencies: defaultdict[str, list[str]] = defaultdict(
        list
    )  # def A references def B
    defs = schema.get("$defs")

    def traverse_and_clean(
        node: object,
        current_def_name: str | None = None,
        skip_defs_section: bool = False,
        depth: int = 0,
    ) -> None:
        """Traverse schema tree, collecting $ref info and applying cleanups."""
        if depth > 50:  # Prevent infinite recursion
            return

        if isinstance(node, dict):
            # Collect $ref references for unused definition removal
            if prune_defs:
                ref = node.get("$ref")  # type: ignore
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    referenced_def = ref.split("/")[-1]
                    if current_def_name:
                        # We're inside a $def, so this is a def->def reference
                        def_dependencies[referenced_def].append(current_def_name)
                    else:
                        # We're in the main schema, so this is a root reference
                        root_refs.add(referenced_def)

            # Apply cleanups
            # Only remove "title" if it's a schema metadata field
            # Schema objects have keywords like "type", "properties", "$ref", etc.
            # If we see these, then "title" is metadata, not a property name
            if prune_titles and "title" in node:
                # Check if this looks like a schema node
                if any(
                    k in node
                    for k in [
                        "type",
                        "properties",
                        "$ref",
                        "items",
                        "allOf",
                        "oneOf",
                        "anyOf",
                        "required",
                    ]
                ):
                    node.pop("title")  # type: ignore

            if (
                prune_additional_properties
                and node.get("additionalProperties") is False  # type: ignore
            ):
                node.pop("additionalProperties")  # type: ignore

            # Recursive traversal
            for key, value in node.items():
                if skip_defs_section and key == "$defs":
                    continue  # Skip $defs during main schema traversal

                # Handle schema composition keywords with special traversal
                if key in ["allOf", "oneOf", "anyOf"] and isinstance(value, list):
                    for item in value:
                        traverse_and_clean(item, current_def_name, depth=depth + 1)
                else:
                    traverse_and_clean(value, current_def_name, depth=depth + 1)

        elif isinstance(node, list):
            for item in node:
                traverse_and_clean(item, current_def_name, depth=depth + 1)

    # Phase 2: Traverse main schema (excluding $defs section)
    traverse_and_clean(schema, skip_defs_section=True)

    # Phase 3: Traverse $defs to find inter-definition references
    if prune_defs and defs:
        for def_name, def_schema in defs.items():
            traverse_and_clean(def_schema, current_def_name=def_name)

        # Phase 4: Remove unused definitions
        def is_def_used(def_name: str, visiting: set[str] | None = None) -> bool:
            """Check if a definition is used, handling circular references."""
            if def_name in root_refs:
                return True  # Used directly from main schema

            # Check if any definition that references this one is itself used
            referencing_defs = def_dependencies.get(def_name, [])
            if referencing_defs:
                if visiting is None:
                    visiting = set()

                # Avoid infinite recursion on circular references
                if def_name in visiting:
                    return False
                visiting = visiting | {def_name}

                # If any referencing def is used, then this def is used
                for referencing_def in referencing_defs:
                    if referencing_def not in visiting and is_def_used(
                        referencing_def, visiting
                    ):
                        return True

            return False

        # Remove unused definitions
        for def_name in list(defs.keys()):
            if not is_def_used(def_name):
                defs.pop(def_name)

        # Clean up empty $defs section
        if not defs:
            schema.pop("$defs", None)

    return schema


def compress_schema(
    schema: dict[str, Any],
    prune_params: list[str] | None = None,
    prune_additional_properties: bool = True,
    prune_titles: bool = False,
) -> dict[str, Any]:
    """
    Compress and optimize a JSON schema for MCP compatibility.

    This function dereferences all $ref entries (inlining definitions) to ensure
    compatibility with MCP clients that don't properly handle $ref in schemas
    (e.g., VS Code Copilot). It also applies various optimizations to reduce
    schema size.

    Args:
        schema: The schema to compress
        prune_params: List of parameter names to remove from properties
        prune_additional_properties: Whether to remove additionalProperties: false
        prune_titles: Whether to remove title fields from the schema
    """
    # Dereference $ref - this inlines all definitions and removes $defs
    # Required for MCP client compatibility
    schema = dereference_refs(schema)

    # Remove specific parameters if requested
    for param in prune_params or []:
        schema = _prune_param(schema, param=param)

    # Apply combined optimizations in a single tree traversal
    if prune_titles or prune_additional_properties:
        schema = _single_pass_optimize(
            schema,
            prune_titles=prune_titles,
            prune_additional_properties=prune_additional_properties,
            prune_defs=False,
        )

    return schema
