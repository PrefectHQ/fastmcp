"""Hypothesis strategies for JSON Schemas and JSON instances.

Schemas draw from a small pool of property and definition names so that
instances drawn from the same pools hit the schema's structure often, and
`$ref`s point at a small set of `$defs` so cycles are common.
"""

from typing import Any

from hypothesis import strategies as st

PROPS = ["a", "b", "c"]
DEFS = ["A", "B"]
STRINGS = ["", "x", "ab", "1", "true", "2024-01-01", "a b"]


def json_values(max_leaves: int = 8) -> st.SearchStrategy[Any]:
    scalars = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(-3, 3),
        st.sampled_from([0.5, -1.5, 2.0, 1e20]),
        st.sampled_from(STRINGS),
    )
    return st.recursive(
        scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=3),
            st.dictionaries(st.sampled_from([*PROPS, "z"]), children, max_size=3),
        ),
        max_leaves=max_leaves,
    )


TYPES = ["string", "integer", "number", "boolean", "null", "array", "object"]


def _leaf() -> st.SearchStrategy[dict[str, Any]]:
    typed = st.sampled_from(TYPES).map(lambda t: {"type": t})
    return st.one_of(
        typed,
        st.lists(st.sampled_from(TYPES), min_size=1, max_size=3, unique=True).map(
            lambda ts: {"type": ts}
        ),
        st.builds(
            lambda lo, hi: {"type": "integer", "minimum": lo, "maximum": hi},
            st.integers(-2, 1),
            st.integers(0, 3),
        ),
        st.builds(
            lambda lo, ex: (
                {"type": "number", "exclusiveMinimum": lo}
                if ex
                else {"type": "number", "multipleOf": 0.5}
            ),
            st.integers(-1, 1),
            st.booleans(),
        ),
        st.builds(
            lambda lo, hi, pat: {
                "type": "string",
                "minLength": lo,
                "maxLength": hi,
                **({"pattern": pat} if pat else {}),
            },
            st.integers(0, 1),
            st.integers(1, 3),
            st.sampled_from([None, "^a", "b$", "[0-9]"]),
        ),
        st.lists(json_values(2), min_size=1, max_size=3).map(lambda v: {"enum": v}),
        json_values(2).map(lambda v: {"const": v}),
        st.sampled_from(DEFS).map(lambda d: {"$ref": f"#/$defs/{d}"}),
        st.just({}),
        # An object whose default is anything, including not an object: the
        # shape default-merging has to tolerate.
        st.sampled_from([None, True, False, 0, 1, "x", [], ["a"]]).map(
            lambda d: {"type": "object", "default": d}
        ),
    )


def _extend(children: st.SearchStrategy[dict[str, Any]]):
    obj = st.builds(
        lambda props, req, addl: {
            "type": "object",
            "properties": props,
            **({"required": sorted(req & props.keys())} if req & props.keys() else {}),
            **({"additionalProperties": addl} if addl is not None else {}),
        },
        st.dictionaries(st.sampled_from(PROPS), children, max_size=3),
        st.sets(st.sampled_from(PROPS)),
        st.one_of(st.none(), st.booleans(), children),
    )
    arr = st.builds(
        lambda items, lo, uniq: {
            "type": "array",
            "items": items,
            **({"minItems": lo} if lo else {}),
            **({"uniqueItems": True} if uniq else {}),
        },
        children,
        st.integers(0, 2),
        st.booleans(),
    )
    prefix = st.builds(
        lambda pre, items: {
            "type": "array",
            "prefixItems": pre,
            **({"items": items} if items is not None else {}),
        },
        st.lists(children, min_size=1, max_size=2),
        st.one_of(st.none(), st.just(False), children),
    )
    combo = st.builds(
        lambda kw, subs: {kw: subs},
        st.sampled_from(["anyOf", "oneOf", "allOf"]),
        st.lists(children, min_size=1, max_size=3),
    )
    nullable = children.map(lambda s: {"anyOf": [s, {"type": "null"}]})
    with_default = st.builds(lambda s, d: {**s, "default": d}, children, json_values(2))
    return st.one_of(obj, arr, prefix, combo, nullable, with_default)


def subschemas() -> st.SearchStrategy[dict[str, Any]]:
    return st.recursive(_leaf(), _extend, max_leaves=10)


@st.composite
def schemas(draw: st.DrawFn) -> dict[str, Any]:
    """A schema with `$defs` for every name `$ref`s may point at."""
    root: dict[str, Any] = draw(subschemas())
    defs = {name: draw(subschemas()) for name in DEFS}
    if draw(st.booleans()):
        # A definition that recurses through a positional array, in a prefix
        # position or the tail, since cycles through containers are where
        # recursive-type resolution has to reach.
        name = draw(st.sampled_from(DEFS))
        ref = {"$ref": f"#/$defs/{name}"}
        in_tail = draw(st.booleans())
        defs[name] = {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "kids": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}]
                    if in_tail
                    else [{"type": "string"}, ref],
                    **({"items": ref} if in_tail else {}),
                },
            },
        }
        # Reach the cycle from the root, as a tool's output schema would.
        root = {
            "type": "object",
            "properties": {
                **(root.get("properties", {}) if root.get("type") == "object" else {}),
                "node": ref,
            },
        }
    if draw(st.booleans()):
        # An object root, the shape a tool's input or output schema has.
        root = {
            "type": "object",
            "properties": {
                p: draw(subschemas())
                for p in draw(st.sets(st.sampled_from(PROPS), min_size=1))
            },
        }
    if root.get("type") == "object" and draw(st.booleans()):
        # An object property with an object or non-object default, the shape
        # default-merging has to handle. With typed extras, an object default
        # can be invalid for the object it is merged into.
        meta: dict[str, Any] = {"type": "object"}
        if draw(st.booleans()):
            meta["additionalProperties"] = {"type": "string"}
            meta["default"] = draw(st.sampled_from([{"a": 1}, {"a": "s"}, {}]))
        else:
            meta["default"] = draw(
                st.sampled_from([None, True, 0, 1, "x", [], ["a"], {}, {"a": 1}])
            )
        root = {
            **root,
            "properties": {**root.get("properties", {}), "meta": meta},
        }
    if root.get("type") == "object" and draw(st.booleans()):
        # A positional array inside an object whose additionalProperties is a
        # schema, inline or through a $ref: the enclosing model's config then
        # differs from a plain object's.
        pair = {
            "type": "array",
            "prefixItems": [{"type": "string"}, {"type": "integer"}],
        }
        if draw(st.booleans()):
            defs = {**defs, "B": pair}
            pair = {"$ref": "#/$defs/B"}
        root = {
            **root,
            "properties": {
                **root.get("properties", {}),
                "x": {
                    "type": "object",
                    "properties": {"pair": pair},
                    "additionalProperties": {"type": "string"},
                },
            },
        }
    return {**root, "$defs": defs}


@st.composite
def shaped_instances(draw: st.DrawFn, schema: dict[str, Any]) -> Any:
    """A value shaped by `schema`, valid more often than random JSON.

    It follows `$ref`, combinators, object properties and positional arrays,
    so values reach nested properties and recursive positions. It is not a
    valid-instance generator: the oracle still decides.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any, depth: int) -> Any:
        if not isinstance(node, dict) or depth > 4:
            return draw(json_values(2))
        if "$ref" in node:
            target = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
            return walk(target, depth + 1)
        for key in ("anyOf", "oneOf", "allOf"):
            if node.get(key):
                return walk(draw(st.sampled_from(node[key])), depth + 1)
        if "const" in node:
            return node["const"]
        if node.get("enum"):
            return draw(st.sampled_from(node["enum"]))
        kind = node.get("type")
        if isinstance(kind, list):
            kind = draw(st.sampled_from(kind)) if kind else None
        if kind == "object" or "properties" in node:
            props = node.get("properties", {})
            # Usually every property, so nested values are present to check.
            every = draw(st.integers(0, 2)) > 0
            if every or not props:
                keys = set(props)
            else:
                keys = draw(st.sets(st.sampled_from(sorted(props))))
            return {k: walk(props[k], depth + 1) for k in sorted(keys)}
        if kind == "array" or "prefixItems" in node:
            prefix = node.get("prefixItems", [])
            n_prefix = draw(st.integers(0, len(prefix)))
            out = [walk(s, depth + 1) for s in prefix[:n_prefix]]
            if n_prefix == len(prefix) and node.get("items") is not False:
                out += [walk(node.get("items", {}), depth + 1)
                        for _ in range(draw(st.integers(0, 2)))]  # fmt: skip
            return out
        simple = {
            "string": st.sampled_from(STRINGS),
            "integer": st.integers(-3, 3),
            "number": st.sampled_from([0.5, 1, -1.5]),
            "boolean": st.booleans(),
            "null": st.none(),
        }
        return draw(simple.get(kind, json_values(2)))

    return walk(schema, 0)
