from __future__ import annotations

from collections import defaultdict
from typing import Any

from jsonref import JsonRefError, replace_refs


def _defs_have_cycles(defs: dict[str, Any]) -> bool:
    """Check whether any definitions in ``$defs`` form a reference cycle.

    A cycle means a definition directly or transitively references itself
    (e.g. Node → children → Node, or A → B → A).  ``jsonref.replace_refs``
    silently produces Python-level object cycles for these, which Pydantic's
    serializer rejects.
    """
    if not defs:
        return False

    # Build adjacency: def_name -> set of def_names it references.
    edges: dict[str, set[str]] = defaultdict(set)

    def _collect_refs(obj: Any, source: str) -> None:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                edges[source].add(ref.split("/")[-1])
            for v in obj.values():
                _collect_refs(v, source)
        elif isinstance(obj, list):
            for item in obj:
                _collect_refs(item, source)

    for name, definition in defs.items():
        _collect_refs(definition, name)

    # DFS cycle detection.
    UNVISITED, IN_STACK, DONE = 0, 1, 2
    state: dict[str, int] = defaultdict(int)

    def _has_cycle(node: str) -> bool:
        state[node] = IN_STACK
        for neighbor in edges.get(node, ()):
            if neighbor not in defs:
                continue
            if state[neighbor] == IN_STACK:
                return True
            if state[neighbor] == UNVISITED and _has_cycle(neighbor):
                return True
        state[node] = DONE
        return False

    return any(state[name] == UNVISITED and _has_cycle(name) for name in defs)


def _strip_remote_refs(obj: Any) -> Any:
    """Return a deep copy of *obj* with non-local ``$ref`` values removed.

    Local refs (starting with ``#``) are kept intact.  Remote refs
    (``http://``, ``https://``, ``file://``, or any other URI scheme) are
    stripped so that ``jsonref.replace_refs`` never attempts to fetch an
    external resource.  This prevents SSRF / LFI when proxying schemas
    from untrusted servers.
    """
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            # Drop the remote $ref key; keep all other keys.
            return {k: _strip_remote_refs(v) for k, v in obj.items() if k != "$ref"}
        return {k: _strip_remote_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_remote_refs(item) for item in obj]
    return obj


def _strip_discriminator(obj: Any) -> Any:
    """Recursively remove OpenAPI ``discriminator`` keys from a schema.

    Pydantic emits ``discriminator.mapping`` with values like
    ``#/$defs/ClassName``.  After ``$defs`` are inlined and removed by
    ``dereference_refs``, those mapping entries dangle.  The keyword is an
    OpenAPI extension — the ``anyOf`` variants already carry ``const`` on
    the discriminant field, so the mapping is redundant.

    Only strips ``discriminator`` when it appears alongside ``anyOf`` or
    ``oneOf``, which is where the OpenAPI keyword lives.  A property
    *named* ``discriminator`` (inside ``properties``) is left alone.
    """
    if isinstance(obj, dict):
        skip = "discriminator" in obj and ("anyOf" in obj or "oneOf" in obj)
        # Keys that hold instance data, not sub-schemas — don't recurse.
        _DATA_KEYS = {"default", "const", "examples", "enum"}
        return {
            k: (v if k in _DATA_KEYS else _strip_discriminator(v))
            for k, v in obj.items()
            if not (k == "discriminator" and skip)
        }
    if isinstance(obj, list):
        return [_strip_discriminator(item) for item in obj]
    return obj


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

    Only local ``$ref`` values (those starting with ``#``) are resolved.
    Remote URIs (``http://``, ``file://``, etc.) are stripped before
    resolution to prevent SSRF / local-file-inclusion attacks when proxying
    schemas from untrusted servers.

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
    # Strip any remote $ref values before processing to prevent SSRF / LFI.
    schema = _strip_remote_refs(schema)

    # Circular $defs can't be fully inlined — jsonref.replace_refs produces
    # Python dicts with object-identity cycles that Pydantic's model_dump
    # rejects with "Circular reference detected (id repeated)".
    # Detect cycles up front and fall back to root-only resolution.
    if _defs_have_cycles(schema.get("$defs", {})):
        return resolve_root_ref(schema)

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

        # Strip `discriminator` keys — they contain `mapping` values that
        # point at `#/$defs/...` entries we just removed.  `discriminator`
        # is an OpenAPI extension; after inlining, the `anyOf` variants
        # already carry `const` on the discriminant field, making the
        # mapping redundant.
        dereferenced = _strip_discriminator(dereferenced)

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


# JSON Schema structural keywords — a node containing any of these is a
# schema, so a string "title" sibling is metadata we can safely drop.
_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "$ref",
        "items",
        "allOf",
        "oneOf",
        "anyOf",
        "required",
    }
)

# Pure schema-metadata keys. A node containing only these (e.g. Pydantic's
# `{"title": "X"}` for Any-typed fields) is also a schema, just one with no
# structural keywords alongside — still safe to strip title from.
_METADATA_KEYS = frozenset(
    {
        "title",
        "description",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

# Keywords whose values are literal user data, not sub-schemas. Skipping
# recursion here prevents `default: {"title": "X"}` from losing the "title"
# data value because it happens to look metadata-shaped. Includes both
# `examples` (JSON Schema draft 7+) and `example` (OpenAPI/Swagger 2.0).
_LITERAL_KEYWORDS = frozenset({"default", "const", "examples", "example", "enum"})

# Keys whose values are dicts of arbitrary-name -> sub-schema. When we see
# these, we traverse into each sub-schema regardless of its name — the keys
# are user property/definition names, not schema keywords, so a property
# literally named "enum" or "default" must not be confused with the
# schema keywords of the same name.
_SUBSCHEMA_MAP_KEYS = frozenset(
    {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
)

# Keys whose values are a single sub-schema (not a dict of sub-schemas).
# We traverse into them and treat the result as a schema node.
_SUBSCHEMA_VALUE_KEYS = frozenset(
    {
        "items",
        "additionalProperties",
        "contains",
        "propertyNames",
        "unevaluatedItems",
        "unevaluatedProperties",
        "if",
        "then",
        "else",
        "not",
    }
)

# Keys whose values are LISTS of sub-schemas.
_SUBSCHEMA_LIST_KEYS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


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
        in_schema: bool = True,
    ) -> None:
        """Traverse schema tree, collecting $ref info and applying cleanups.

        The `in_schema` flag tracks whether the current node is reached via a
        known JSON-Schema-valued position (root, `properties` value, `items`,
        `allOf` element, etc.). When False — e.g. we descended through a user
        extension key like `x-ui` whose payload is opaque to us — we still
        collect `$ref` references (they may point at `$defs` the user cares
        about) but we skip all cleanups so we don't mutate user data that
        happens to look metadata-shaped.
        """
        if depth > 50:  # Prevent infinite recursion
            return

        if isinstance(node, dict):
            # Collect $ref references for unused definition removal. We do
            # this regardless of `in_schema` — a $ref in a user extension
            # still pins the referenced $def as "used".
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

            # Cleanups only run when we know this node is a schema, never on
            # user extension payloads (`json_schema_extra={"x-ui": {...}}`).
            if in_schema:
                # Only remove "title" when it's schema metadata. A schema
                # node is either (a) one containing a structural keyword or
                # (b) one containing only metadata keys — Pydantic emits
                # bare `{"title": "X"}` for Any-typed fields with no sibling
                # type/properties, and Gemini 2.5 Flash rejects those with
                # MALFORMED_FUNCTION_CALL. The `isinstance(str)` guard
                # protects against deleting a user property literally named
                # "title" (its value would be a dict, not a string).
                if (
                    prune_titles
                    and "title" in node
                    and isinstance(node["title"], str)  # type: ignore
                    and (
                        any(k in node for k in _SCHEMA_KEYWORDS)
                        or all(k in _METADATA_KEYS for k in node)
                    )
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

                # If we're not in a schema context, keep $ref-collecting but
                # don't promote sub-values to schema context — user extension
                # payloads can contain anything and must not be interpreted
                # as schemas.
                if not in_schema:
                    traverse_and_clean(
                        value, current_def_name, depth=depth + 1, in_schema=False
                    )
                    continue

                # Arbitrary-key dicts of sub-schemas. The keys are user names
                # (property/definition names), not schema keywords, so we
                # must NOT apply the literal-keyword skip to them — a user
                # property named "enum" or "default" still needs its
                # sub-schema traversed (e.g. to collect $ref references).
                if key in _SUBSCHEMA_MAP_KEYS and isinstance(value, dict):
                    for sub_schema in value.values():
                        traverse_and_clean(
                            sub_schema,
                            current_def_name,
                            depth=depth + 1,
                            in_schema=True,
                        )
                    continue

                # Don't descend into keywords that carry literal data, not
                # sub-schemas — `default: {"title": "X"}` is a user value,
                # not schema metadata, and stripping "title" there would
                # corrupt it.
                if key in _LITERAL_KEYWORDS:
                    continue

                # Keywords whose values are sub-schemas (or lists thereof).
                if key in _SUBSCHEMA_LIST_KEYS and isinstance(value, list):
                    for item in value:
                        traverse_and_clean(
                            item,
                            current_def_name,
                            depth=depth + 1,
                            in_schema=True,
                        )
                    continue

                if key in _SUBSCHEMA_VALUE_KEYS:
                    traverse_and_clean(
                        value,
                        current_def_name,
                        depth=depth + 1,
                        in_schema=True,
                    )
                    continue

                # Unknown keys (user extensions like `x-ui`, vendor
                # metadata, etc.) — descend for $ref collection but mark
                # in_schema=False so cleanups don't touch user payloads.
                traverse_and_clean(
                    value, current_def_name, depth=depth + 1, in_schema=False
                )

        elif isinstance(node, list):
            for item in node:
                traverse_and_clean(
                    item, current_def_name, depth=depth + 1, in_schema=in_schema
                )

    # Phase 2: Traverse main schema (excluding $defs section)
    traverse_and_clean(schema, skip_defs_section=True, in_schema=True)

    # Phase 3: Traverse $defs to find inter-definition references
    if prune_defs and defs:
        for def_name, def_schema in defs.items():
            traverse_and_clean(def_schema, current_def_name=def_name, in_schema=True)

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
    prune_additional_properties: bool = False,
    prune_titles: bool = False,
    dereference: bool = False,
) -> dict[str, Any]:
    """
    Compress and optimize a JSON schema for MCP compatibility.

    Args:
        schema: The schema to compress
        prune_params: List of parameter names to remove from properties
        prune_additional_properties: Whether to remove additionalProperties: false.
            Defaults to False to maintain MCP client compatibility, as some clients
            (e.g., Claude) require additionalProperties: false for strict validation.
        prune_titles: Whether to remove title fields from the schema
        dereference: Whether to dereference $ref by inlining definitions.
            Defaults to False; dereferencing is typically handled by
            middleware at serve-time instead.
    """
    if dereference:
        schema = dereference_refs(schema)

    # Resolve root-level $ref for MCP spec compliance (requires type: object at root)
    schema = resolve_root_ref(schema)

    # Remove specific parameters if requested
    for param in prune_params or []:
        schema = _prune_param(schema, param=param)

    # Apply combined optimizations in a single tree traversal.
    # Always prune unused $defs to keep schemas clean after parameter removal.
    schema = _single_pass_optimize(
        schema,
        prune_titles=prune_titles,
        prune_additional_properties=prune_additional_properties,
        prune_defs=True,
    )

    return schema
