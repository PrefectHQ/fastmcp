"""Schema rewriting preserves what validates; schema-to-type accepts what is valid.

The oracle is `jsonschema` (Draft 2020-12), which shares no code with
fastmcp's schema utilities.

- `dereference` / `compress`: the rewritten schema accepts exactly the
  instances the original accepts.
- `adapter_complete`: an instance the original schema accepts is accepted by
  `json_schema_to_type_adapter`, the path the client uses to validate a
  tool's structured output. The reverse direction is not asserted: pydantic's
  lax mode coerces `"1"` to `1` on purpose.
"""

import sys
import warnings
from typing import Any

import pytest
from hypothesis import assume, event, given, note, reject
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import PydanticUserError, ValidationError

from fastmcp.utilities.json_schema import compress_schema, dereference_refs
from fastmcp.utilities.json_schema_type import json_schema_to_type_adapter

from .known import still_open
from .schema_strategies import json_values, schemas, shaped_instances


def oracle(schema: dict[str, Any], *, drawn: bool = True):
    """Validity under Draft 2020-12.

    A drawn schema the oracle cannot judge (malformed, or an unguarded `$ref`
    cycle) is rejected as an example. A rewritten schema gets no such pass:
    if a rewrite turns a judgeable schema into one that is not, that is the bug.
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        if drawn:
            reject()
        raise
    validator = Draft202012Validator(schema)

    def is_valid(instance: Any) -> bool:
        try:
            return validator.is_valid(instance)
        except RecursionError:
            if drawn:
                reject()
            raise AssertionError("the schema recurses without end") from None

    return is_valid


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _reproduces(
    schema: dict[str, Any], instance: Any, failure: type[Exception]
) -> bool:
    """True while the finding's minimal case still fails the way it was recorded.

    Any other exception propagates, so an unrelated break in the adapter fails
    the import loudly instead of silently widening every skip.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            json_schema_to_type_adapter(schema).validate_python(instance)
    except failure:
        return True
    return False


_RECURSIVE_TAIL_NODE = {
    "type": "object",
    "properties": {
        "kids": {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {"$ref": "#/$defs/N"},
        }
    },
}
# Findings fixed on main in #6552 (a non-object default crashed building the type),
# #6553 (a recursive positional array left it unbuildable), #6582 and #6572
# (a valid instance was rejected).
# A finding is skipped only while it still reproduces in this tree; once it is
# fixed the harness checks it like everything else. A mutant that undoes a fix
# sets PBT_ASSUME_FIXED so the probe does not mistake it for the open bug
# (see known.py).
NON_OBJECT_DEFAULT_OPEN = still_open(
    "#6552",
    lambda: _reproduces(
        {"type": "object", "properties": {"a": {"type": "object", "default": None}}},
        {"a": {}},
        TypeError,
    ),
)
REF_IN_POSITIONAL_ARRAY_OPEN = still_open(
    "#6553",
    lambda: _reproduces(
        {**_RECURSIVE_TAIL_NODE, "$defs": {"N": _RECURSIVE_TAIL_NODE}},
        {},
        PydanticUserError,
    ),
)
DEFAULT_MERGED_INTO_OBJECT_OPEN = still_open(
    "#6582",
    lambda: _reproduces(
        {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": {"type": "string"},
                    "default": {"a": None},
                }
            },
        },
        {"a": {}},
        ValidationError,
    ),
)
PREFIX_ARRAY_UNDER_EXTRAS_OPEN = still_open(
    "#6572",
    lambda: _reproduces(
        {
            "type": "object",
            "properties": {
                "x": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "array", "prefixItems": [{"type": "string"}]}
                    },
                    "additionalProperties": {"type": "string"},
                }
            },
        },
        {"x": {"a": []}},
        ValidationError,
    ),
)


def adapter_known_divergence(schema: dict[str, Any]) -> str | None:
    """Name a known finding that still reproduces, so the run stops on anything new."""
    nodes = list(_walk(schema))
    if NON_OBJECT_DEFAULT_OPEN and any(
        node.get("type") == "object"
        and "default" in node
        and not isinstance(node["default"], dict)
        for node in nodes
    ):
        return "#6552 object with a non-object default"
    # #6553 fixed a `$ref` cycle through a positional array, in a prefix
    # position or in the `items` tail.
    if REF_IN_POSITIONAL_ARRAY_OPEN and any(
        "prefixItems" in node
        and "$ref" in str(node["prefixItems"]) + str(node.get("items"))
        for node in nodes
    ):
        return "#6553 ref in a positional array"
    if DEFAULT_MERGED_INTO_OBJECT_OPEN and any(
        isinstance(n.get("default"), dict)
        and n["default"]
        and n.get("type") == "object"
        for n in nodes
    ):
        return "#6582 object default merged into a provided object"
    if PREFIX_ARRAY_UNDER_EXTRAS_OPEN and any(
        _typed_extras_hold_prefix_array(n, schema) for n in nodes
    ):
        return "#6572 prefix array under typed additionalProperties"
    return None


def _typed_extras_hold_prefix_array(
    node: dict[str, Any], schema: dict[str, Any]
) -> bool:
    """An object with schema-valued `additionalProperties` whose own properties
    reach an unpinned positional array, directly or through a local `$ref`:
    the shape #6572 fixed."""
    if not isinstance(node.get("additionalProperties"), dict):
        return False
    defs = schema.get("$defs", {})
    seen: set[str] = set()
    stack = list(node.get("properties", {}).values())
    while stack:
        for sub in _walk(stack.pop()):
            if "prefixItems" in sub:
                return True
            ref = sub.get("$ref", "")
            name = ref.rsplit("/", 1)[-1]
            if ref.startswith("#/$defs/") and name not in seen:
                seen.add(name)
                stack.append(defs.get(name, {}))
    return False


def shape_events(schema: dict[str, Any], valid: list[Any]) -> None:
    """Tally how often drawn cases reach the shapes the findings needed."""
    nodes = list(_walk(schema))
    for node in nodes:
        for prop in node.get("properties", {}).values():
            if (
                isinstance(prop, dict)
                and prop.get("type") == "object"
                and "default" in prop
                and not isinstance(prop["default"], dict)
            ):
                event("shape: object property with non-object default")
    if any(
        "prefixItems" in n and "$ref" in str(n.get("prefixItems")) + str(n.get("items"))
        for n in nodes
    ):
        event("shape: $ref inside a positional array")
    event(f"valid instances: {min(len(valid), 3)}+")


# The adapter's documented refusals; any other ValueError is a bug.
REFUSALS = (
    "JSON schema is too deeply nested to convert",
    "JSON schema is too large to convert",
)

INSTANCES = st.lists(json_values(), min_size=1, max_size=8)


def _rewrite_preserves(rewrite, schema: dict[str, Any], instances: list[Any]) -> None:
    before = oracle(schema)
    verdicts = [before(i) for i in instances]
    event(
        "instances: mixed"
        if 0 < sum(verdicts) < len(verdicts)
        else f"instances: all {all(verdicts)}"
    )
    rewritten = rewrite(schema)
    note(f"schema: {schema}")
    note(f"rewritten: {rewritten}")
    event("rewrite changed schema" if rewritten != schema else "rewrite no-op")
    after = oracle(rewritten, drawn=False)
    for instance, verdict in zip(instances, verdicts):
        note(f"instance: {instance!r} original says {verdict}")
        assert after(instance) == verdict, "the rewrite changed what validates"


@given(schemas(), INSTANCES)
def test_dereference_preserves_validation(schema, instances):
    _rewrite_preserves(dereference_refs, schema, instances)


@given(schemas(), INSTANCES, st.booleans(), st.booleans())
def test_compress_preserves_validation(schema, instances, titles, deref):
    _rewrite_preserves(
        lambda s: compress_schema(s, prune_titles=titles, dereference=deref),
        schema,
        instances,
    )


@st.composite
def schema_and_instances(draw: st.DrawFn):
    schema = draw(schemas())
    shaped = draw(st.lists(shaped_instances(schema), min_size=1, max_size=6))
    return schema, shaped + draw(st.lists(json_values(), max_size=2))


@given(schema_and_instances())
def test_adapter_accepts_valid_instances(drawn):
    schema, instances = drawn
    is_valid = oracle(schema)
    valid = [i for i in instances if is_valid(i)]
    shape_events(schema, valid)
    assume(valid)
    if known := adapter_known_divergence(schema):
        event(f"known: {known}")
        return
    note(f"schema: {schema}")
    limit = sys.getrecursionlimit()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            adapter = json_schema_to_type_adapter(schema)
    except ValueError as e:
        if not str(e).startswith(REFUSALS):
            raise
        event(f"refused: {e}")
        return
    assert sys.getrecursionlimit() == limit
    for instance in valid:
        note(f"instance: {instance!r}")
        try:
            adapter.validate_python(instance)
        except Exception as e:
            note(f"rejected with: {e!r}"[:600])
            pytest.fail("a valid instance is rejected")
