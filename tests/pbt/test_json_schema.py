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

import os
import sys
import warnings
from typing import Any

import pytest
from hypothesis import assume, event, given, note, reject
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from fastmcp.utilities.json_schema import compress_schema, dereference_refs
from fastmcp.utilities.json_schema_type import json_schema_to_type_adapter

from .schema_strategies import json_values, schemas, shaped_instances


def oracle(schema: dict[str, Any]):
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        reject()
    validator = Draft202012Validator(schema)

    def is_valid(instance: Any) -> bool:
        try:
            return validator.is_valid(instance)
        except RecursionError:
            # An unguarded `$ref` cycle; the oracle cannot answer.
            reject()

    return is_valid


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _reproduces(schema: dict[str, Any], instance: Any) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            json_schema_to_type_adapter(schema).validate_python(instance)
    except Exception:
        return True
    return False


_F3_NODE = {
    "type": "object",
    "properties": {
        "kids": {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {"$ref": "#/$defs/N"},
        }
    },
}
# A finding is skipped only while it still reproduces in this tree; once it is
# fixed the harness checks it like everything else. A mutant that undoes a fix
# sets PBT_ASSUME_FIXED so the probe does not mistake it for the open bug.
F1_OPEN = "F1" not in os.environ.get("PBT_ASSUME_FIXED", "") and _reproduces(
    {"type": "object", "properties": {"a": {"type": "object", "default": None}}},
    {"a": {}},
)
F3_OPEN = "F3" not in os.environ.get("PBT_ASSUME_FIXED", "") and _reproduces(
    {**_F3_NODE, "$defs": {"N": _F3_NODE}}, {}
)
F2_OPEN = "F2" not in os.environ.get("PBT_ASSUME_FIXED", "") and _reproduces(
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
)
F5_OPEN = "F5" not in os.environ.get("PBT_ASSUME_FIXED", "") and _reproduces(
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
)


def adapter_known_divergence(schema: dict[str, Any]) -> str | None:
    """Name an open finding recorded in FINDINGS.md, so the run stops on anything new."""
    nodes = list(_walk(schema))
    if F1_OPEN and any(
        node.get("type") == "object"
        and "default" in node
        and not isinstance(node["default"], dict)
        for node in nodes
    ):
        return "F1-object-non-dict-default"
    if F3_OPEN and any("prefixItems" in node and "$ref" in str(node) for node in nodes):
        return "F3-ref-in-prefix-array"
    if F2_OPEN and any(
        isinstance(n.get("default"), dict)
        and n["default"]
        and n.get("type") == "object"
        for n in nodes
    ):
        return "F2-object-default-merged-into-provided-object"
    if F5_OPEN and any(_typed_extras_hold_prefix_array(n, schema) for n in nodes):
        return "F5-prefix-array-under-typed-additional-properties"
    return None


def _typed_extras_hold_prefix_array(
    node: dict[str, Any], schema: dict[str, Any]
) -> bool:
    """An object with schema-valued `additionalProperties` whose own properties
    reach an unpinned positional array, directly or through a local `$ref`:
    the shape F5 needs."""
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
    after = oracle(rewritten)
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
        # Documented refusals: too deep, too large.
        event(f"refused: {str(e)[:40]}")
        return
    assert sys.getrecursionlimit() == limit
    for instance in valid:
        note(f"instance: {instance!r}")
        try:
            adapter.validate_python(instance)
        except Exception as e:
            note(f"rejected with: {e!r}"[:600])
            pytest.fail("a valid instance is rejected")
