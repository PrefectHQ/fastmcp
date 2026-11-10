"""Constraints on uses of shared scalar definitions."""

from copy import deepcopy
from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import (
    json_schema_to_type,
    json_schema_to_type_adapter,
)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    ("definition", "siblings", "valid", "invalid"),
    [
        ({"type": "string"}, {"maxLength": 3}, "abc", "abcd"),
        ({"type": "string", "minLength": 3}, {"minLength": 1}, "abc", "ab"),
        ({"type": "string", "pattern": "^a"}, {"pattern": "z$"}, "az", "ab"),
        ({"type": "string", "pattern": "^a"}, {"pattern": "z$"}, "az", "bz"),
        ({"type": "integer", "minimum": 0}, {"maximum": 5}, 5, 6),
        ({"type": "integer", "minimum": 5}, {"minimum": 0}, 5, 4),
        ({"type": "number", "maximum": 5}, {"maximum": 10}, 5, 6),
        ({"type": "integer", "multipleOf": 2}, {"multipleOf": 3}, 6, 4),
        ({"type": "integer", "multipleOf": 2}, {"multipleOf": 3}, 6, 3),
        ({"type": "integer"}, {"enum": [2, 3]}, 2, 4),
        ({"type": "integer", "minimum": 5}, {"enum": [2, 6]}, 6, 2),
        ({"type": "integer"}, {"const": 2}, 2, 3),
        ({"type": "number"}, {"type": "integer"}, 2, 2.5),
        (
            {"type": "string", "format": "date-time"},
            {"maxLength": 20},
            "2026-10-26T00:00:00Z",
            "2026-10-26T00:00:00.000000+00:00",
        ),
    ],
)
def test_scalar_reference_constraints(definition, siblings, valid, invalid, cached):
    schema = {"$defs": {"Value": definition}, "$ref": "#/$defs/Value", **siblings}
    original = deepcopy(schema)
    adapter = (
        json_schema_to_type_adapter(schema)
        if cached
        else TypeAdapter(json_schema_to_type(schema))
    )
    result = adapter.validate_python(valid)
    if definition.get("format") == "date-time":
        assert isinstance(result, datetime)
    else:
        assert result == valid
    with pytest.raises(ValidationError):
        adapter.validate_python(invalid)
    assert schema == original


@pytest.mark.parametrize("model", [False, True])
def test_reference_constraints_do_not_change_other_uses(model):
    schema = {
        "type": "object",
        "properties": {
            "short": {"$ref": "#/$defs/Text", "maxLength": 2},
            "long": {"$ref": "#/$defs/Text", "minLength": 3},
            "plain": {"$ref": "#/$defs/Text"},
        },
        "required": ["short", "long", "plain"],
        "$defs": {"Text": {"type": "string", "minLength": 1}},
    }
    if model:
        schema["additionalProperties"] = True
    adapter = TypeAdapter(json_schema_to_type(schema))
    valid = {"short": "ab", "long": "abcd", "plain": "anything"}
    result = adapter.validate_python(valid)
    assert result.short == "ab"
    assert result.long == "abcd"
    assert result.plain == "anything"
    for changed in ({"short": "abc"}, {"long": "ab"}, {"plain": ""}):
        with pytest.raises(ValidationError):
            adapter.validate_python({**valid, **changed})


def test_constraints_on_transitive_scalar_reference():
    schema = {
        "$defs": {
            "Text": {"type": "string", "minLength": 2},
            "Alias": {"$ref": "#/$defs/Text"},
        },
        "$ref": "#/$defs/Alias",
        "maxLength": 3,
    }
    adapter = TypeAdapter(json_schema_to_type(schema))
    assert adapter.validate_python("abc") == "abc"
    for value in ("a", "abcd"):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


def test_reference_annotations_do_not_wrap_plain_type():
    schema = {
        "$defs": {"Text": {"type": "string"}},
        "$ref": "#/$defs/Text",
        "description": "A shared string",
    }
    assert json_schema_to_type(schema) is str


@pytest.mark.parametrize(
    "draft",
    [
        "http://json-schema.org/draft-04/schema#",
        "http://json-schema.org/draft-06/schema#",
        "http://json-schema.org/draft-07/schema#",
    ],
)
def test_legacy_reference_siblings_remain_ignored(draft):
    schema = {
        "$schema": draft,
        "definitions": {"Text": {"type": "string"}},
        "$ref": "#/definitions/Text",
        "maxLength": 1,
    }
    assert TypeAdapter(json_schema_to_type(schema)).validate_python("long") == "long"
