from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from contextlib import suppress
from typing import Any
from urllib.parse import unquote

from jsonref import JsonRefError, replace_refs


def _copy_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of a JSON schema without recursing.

    `copy.deepcopy` consumes stack frames in proportion to nesting depth, so a
    deeply nested schema raises `RecursionError` before the traversals in this
    module can apply their own depth guards — turning a schema that used to
    compress into one that fails outright. Schemas are plain JSON, so an
    explicit stack copies the containers at any depth and shares the immutable
    scalars at the leaves.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[Any, Any]] = [(schema, root)]

    while stack:
        source, target = stack.pop()
        if isinstance(source, dict):
            pairs: list[tuple[Any, Any]] = list(source.items())
        else:
            pairs = list(enumerate(source))

        for key, value in pairs:
            if isinstance(value, dict):
                child: Any = {}
                stack.append((value, child))
            elif isinstance(value, list):
                child = [None] * len(value)
                stack.append((value, child))
            else:
                child = value
            target[key] = child

    return root


# Inlining a `$ref` copies its target, so an acyclic reference graph can expand
# to many times its own size. `dereference_refs` only inlines when the result
# stays within these limits; larger graphs keep their references.
_MAX_INLINED_NODES = 100_000
_MAX_INLINED_DEPTH = 128
_MAX_INLINED_TEXT = 5_000_000
_MAX_ROOT_REF_HOPS = 32


class _CannotInline(Exception):
    """A schema's local references can't be inlined within the limits."""


def _resolve_local_ref(
    schema: dict[str, Any], ref: str, _hops: list[int] | None = None
) -> Any:
    """Resolve a local `$ref` (`#...`) by walking its JSON pointer through *schema*.

    This uses the same pointer syntax as `jsonref` but doesn't normalize the
    reference as a URL, so it can pick a different target than `jsonref` for
    unusual pointers. `dereference_refs` therefore measures the result
    `jsonref` actually built as well.

    Like `jsonref`, a pointer that reaches a node that is itself a local
    `$ref` continues through that reference's target, so
    `#/$defs/Alias/properties/x` resolves when `Alias` refers to another
    definition. The number of such references followed during one lookup is
    limited, which also stops reference cycles. The root itself is never
    followed, so a root-level `$ref` doesn't redirect its own pointers.
    """
    hops = _hops if _hops is not None else [0]
    fragment = ref[1:]
    parts = unquote(fragment.lstrip("/")).split("/") if fragment else []
    node: Any = schema
    for part in parts:
        while node is not schema and isinstance(node, dict) and "$ref" in node:
            inner = node["$ref"]
            if not isinstance(inner, str) or not inner.startswith("#"):
                break
            hops[0] += 1
            if hops[0] > _MAX_ROOT_REF_HOPS:
                raise _CannotInline(f"Too many references in pointer: {ref}")
            node = _resolve_local_ref(schema, inner, hops)
        key: str | int = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Sequence):
            with suppress(ValueError):
                key = int(key)
        try:
            node = node[key]
        except (LookupError, TypeError) as e:
            raise _CannotInline(f"Unresolvable reference: {ref}") from e
    return node


def _within_inline_limits(root: dict[str, Any], *, follow_refs: bool) -> bool:
    """Check the node count and depth of *root* once fully expanded.

    Containers that appear in several places are measured once and the result
    reused, so the walk is linear in the number of distinct containers.
    Reaching a container again while it is still being measured means there
    is a cycle, which can't be expanded.

    With *follow_refs*, each local `$ref` also counts the target found by
    `_resolve_local_ref`, which measures what inlining *root* would produce
    without building it. The measurement covers the whole schema, including
    unused `$defs` entries and keywords next to `$ref`, because
    `dereference_refs` processes both. Without *follow_refs*, `$ref` values
    are plain data, which measures a structure that is already inlined.
    """
    # (nodes, height) of each measured container, by id.
    sizes: dict[int, tuple[int, int]] = {}
    in_progress: set[int] = set()

    def measure(node: dict[str, Any] | list[Any], depth: int) -> tuple[int, int]:
        if depth > _MAX_INLINED_DEPTH:
            raise _CannotInline("Inlined schema is too deep")
        identity = id(node)
        if identity in sizes:
            nodes, height = sizes[identity]
        elif identity in in_progress:
            raise _CannotInline("References form a cycle")
        else:
            in_progress.add(identity)
            children = list(node.values()) if isinstance(node, dict) else node
            if follow_refs and isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#"):
                    children = [*children, _resolve_local_ref(root, ref)]
            nodes, height = 1, 0
            for child in children:
                if isinstance(child, dict | list):
                    child_nodes, child_height = measure(child, depth + 1)
                    nodes += child_nodes
                    height = max(height, child_height + 1)
                else:
                    nodes += 1
                if nodes > _MAX_INLINED_NODES:
                    raise _CannotInline("Inlined schema is too large")
            in_progress.remove(identity)
            sizes[identity] = nodes, height
        if depth + height > _MAX_INLINED_DEPTH:
            raise _CannotInline("Inlined schema is too deep")
        return nodes, height

    try:
        measure(root, 0)
    except (_CannotInline, RecursionError):
        return False
    return True


def _text_within_limit(schema: dict[str, Any]) -> bool:
    """Check the total length of every key and scalar value in *schema*.

    Inlining shares scalar objects rather than copying them, so a repeated
    long string or large number costs little to build but still adds to the
    serialized size. Numbers count at least their decimal digits and sign.
    """
    total = 0
    stack: list[Any] = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            total += sum(len(key) for key in node if isinstance(key, str))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            total += len(node)
        elif isinstance(node, bool) or node is None:
            total += 5
        elif isinstance(node, int):
            # A bound on the decimal digits that avoids converting huge ints.
            total += node.bit_length() // 3 + 2
        elif isinstance(node, float):
            total += len(repr(node))
        if total > _MAX_INLINED_TEXT:
            return False
    return True


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
        if skip:
            obj = require_discriminator_property(obj)
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


def _require_property(schema: dict[str, Any], property_name: str) -> dict[str, Any]:
    """Return a copy of *schema* with *property_name* in ``required``."""
    required = schema.get("required")
    if required is None:
        return {**schema, "required": [property_name]}
    if isinstance(required, list) and property_name not in required:
        return {**schema, "required": [*required, property_name]}
    return schema


def require_discriminator_property(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep an OpenAPI discriminator's tag mandatory after the keyword is dropped.

    Returns a copy of *schema* with ``discriminator.propertyName`` added to each
    ``anyOf``/``oneOf`` variant's ``required`` list. A Pydantic discriminated
    union whose tag has a default omits that tag from ``required``; without this,
    an untagged payload passes the generated schema but fails later in the source
    model with ``union_tag_not_found``. No-op if there is no string
    ``propertyName``.
    """
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, dict):
        return schema
    property_name = discriminator.get("propertyName")
    if not isinstance(property_name, str):
        return schema

    result = schema.copy()
    for key in ("anyOf", "oneOf"):
        variants = result.get(key)
        if not isinstance(variants, list):
            continue
        result[key] = [
            _require_property(variant, property_name)
            if isinstance(variant, dict)
            else variant
            for variant in variants
        ]
    return result


def dereference_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve all $ref references in a JSON schema by inlining definitions.

    This function resolves $ref references that point to $defs, replacing them
    with the actual definition content while preserving sibling keywords (like
    description, default, examples) that Pydantic places alongside $ref.

    This is necessary because some MCP clients (e.g., VS Code Copilot) don't
    properly handle $ref in tool input schemas.

    For circular schemas, and for schemas whose inlined form would be very large
    or deeply nested, this function falls back to resolving only the root-level
    $ref while preserving $defs for nested references.

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

    # Check before inlining anything. Circular references can't be inlined:
    # jsonref.replace_refs produces Python dicts with object-identity cycles
    # that Pydantic's model_dump rejects. Acyclic graphs can still expand far
    # beyond their own size, so oversized results are refused as well.
    if not _within_inline_limits(schema, follow_refs=True):
        return resolve_root_ref(schema)

    try:
        # Use jsonref to resolve all $ref references
        # proxies=False returns plain dicts (not proxy objects)
        # lazy_load=False resolves immediately
        dereferenced = replace_refs(schema, proxies=False, lazy_load=False)

        # Merge sibling keywords that were lost during dereferencing
        # Pydantic puts description, default, examples as siblings to $ref
        merged = _merge_ref_siblings(schema, dereferenced, schema)
        # Type assertion: top-level schema is always a dict
        assert isinstance(merged, dict)
        dereferenced = merged

        # Remove $defs since all references have been resolved
        if "$defs" in dereferenced:
            dereferenced = {k: v for k, v in dereferenced.items() if k != "$defs"}

        # jsonref can resolve some pointers to different targets than the
        # check above, so measure what it built (shared containers, not yet
        # copied) before the discriminator pass copies the whole tree.
        if not _within_inline_limits(dereferenced, follow_refs=False):
            return resolve_root_ref(schema)

        # Strip `discriminator` keys — they contain `mapping` values that
        # point at `#/$defs/...` entries we just removed.  `discriminator`
        # is an OpenAPI extension; after inlining, the `anyOf` variants
        # already carry `const` on the discriminant field, making the
        # mapping redundant.
        dereferenced = _strip_discriminator(dereferenced)

        if not _text_within_limit(dereferenced):
            return resolve_root_ref(schema)
        return dereferenced

    except (JsonRefError, RecursionError, _CannotInline):
        # Self-referencing/circular schemas can't be fully dereferenced.
        # RecursionError covers circular $ref using JSON Pointer paths
        # (e.g. "#/properties/nodes/items") that bypass $defs-based cycle
        # detection — common in schemas from .NET/System.Text.Json.
        # Fall back to resolving only root-level $ref (for MCP spec compliance)
        return resolve_root_ref(schema)


def _merge_ref_siblings(
    original: Any,
    dereferenced: Any,
    schema: dict[str, Any],
    visited: set[str] | None = None,
) -> Any:
    """Merge sibling keywords from original $ref nodes into dereferenced schema.

    When jsonref resolves $ref, it replaces the entire node with the referenced
    definition, losing any sibling keywords like description, default, or examples.
    This function walks both trees in parallel and merges those siblings back.

    Args:
        original: The original schema with $ref and potential siblings
        dereferenced: The schema after jsonref processing
        schema: The original root schema, for looking up referenced definitions
        visited: Set of references already being processed (prevents cycles)

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

            # Process the definition jsonref inlined here for its nested siblings.
            # Prevent infinite recursion on circular references.
            if (
                isinstance(ref, str)
                and ref.startswith("#/$defs/")
                and ref not in visited
            ):
                dereferenced = _merge_ref_siblings(
                    _resolve_local_ref(schema, ref),
                    dereferenced,
                    schema,
                    visited | {ref},
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
                result[key] = _merge_ref_siblings(original[key], value, schema, visited)
            else:
                result[key] = value
        return result

    elif isinstance(original, list) and isinstance(dereferenced, list):
        # Process list items in parallel
        min_len = min(len(original), len(dereferenced))
        return [
            _merge_ref_siblings(o, d, schema, visited)
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
    # Only resolve a local $ref at the root when there is no explicit type.
    # The pointer is walked the same way `dereference_refs` measures it, so
    # every supported form (`#/$defs/Name`, `#/definitions/Name`, escaped or
    # percent-encoded segments) resolves to the same target. A definition
    # that is itself only a `$ref` is an alias, so the chain is followed
    # until a definition with its own content (or a type) is reached.
    ref = schema.get("$ref")
    if "type" in schema or not isinstance(ref, str) or not ref.startswith("#"):
        return schema

    # Preserve root-level sibling metadata from the original schema.
    # Pydantic may put user-facing fields such as title, description,
    # default, or examples next to the root $ref. Those fields still
    # describe the root schema even when we can only resolve that
    # root reference for circular schemas. Siblings include `$defs` (or
    # `definitions`), which stay for nested references. Keywords on each
    # alias are kept too, with the outer schema taking precedence.
    resolved = {key: value for key, value in schema.items() if key != "$ref"}
    visited: set[str] = set()
    for _ in range(_MAX_ROOT_REF_HOPS):
        if ref in visited:
            return schema
        visited.add(ref)
        try:
            target = _resolve_local_ref(schema, ref)
        except _CannotInline:
            return schema
        if not isinstance(target, dict) or target is schema:
            return schema

        next_ref = target.get("$ref")
        is_alias = (
            "type" not in target
            and isinstance(next_ref, str)
            and next_ref.startswith("#")
        )
        if is_alias:
            kept = {key: value for key, value in target.items() if key != "$ref"}
        else:
            kept = target
        resolved = {**kept, **resolved}
        if not is_alias:
            return resolved
        ref = next_ref
    return schema


def _prune_param(schema: dict[str, Any], param: str) -> dict[str, Any]:
    """Return a new schema with *param* removed from `properties`, `required`,
    and (if no longer referenced) `$defs`.
    """
    schema = _copy_schema(schema)

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
#
# `dependencies` is a draft-07 keyword whose values can be sub-schemas OR
# lists of required property names; list values short-circuit in the list
# branch, so including it here is safe for both shapes.
_SUBSCHEMA_MAP_KEYS = frozenset(
    {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    }
)

# Keys whose values are a single sub-schema (not a dict of sub-schemas).
# We traverse into them and treat the result as a schema node.
# `additionalItems` is the draft-07 predecessor of `unevaluatedItems`.
# `contentSchema` is a 2019-09+ keyword for typed string payloads.
_SUBSCHEMA_VALUE_KEYS = frozenset(
    {
        "items",
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
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

    # Work on a copy so the caller's schema is never mutated (see docstring). The
    # pruning phases below pop keys/$defs in place, which would otherwise corrupt a
    # shared dict such as a live Tool.input_schema passed straight to compress_schema.
    schema = _copy_schema(schema)

    # Phase 1: Collect references and apply simple cleanups
    # Track which $defs are referenced from the main schema and from other $defs
    root_refs: set[str] = set()  # $defs referenced directly from main schema
    def_dependencies: defaultdict[str, list[str]] = defaultdict(
        list
    )  # def A references def B
    defs = schema.get("$defs")

    # Set when the traversal below gives up at its depth limit. Once that
    # happens the reference scan is incomplete, so we can no longer tell which
    # definitions are genuinely unused.
    reference_scan_truncated = False

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
        nonlocal reference_scan_truncated

        if depth > 50:  # Prevent infinite recursion
            reference_scan_truncated = True
            return

        if isinstance(node, dict):
            # Collect $ref references for unused definition removal. We do
            # this regardless of `in_schema` — a $ref in a user extension
            # still pins the referenced $def as "used".
            if prune_defs:
                ref = node.get("$ref")
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
                    and node.get("additionalProperties") is False
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

        # An incomplete scan has not seen every $ref, so a definition that looks
        # unused may simply be referenced below the cutoff. Keeping an unused
        # definition is harmless; dropping a referenced one leaves a dangling
        # $ref and an invalid schema.
        if reference_scan_truncated:
            return schema

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
