"""`x-unsupported-pattern` marks a dropped pattern for every string shape.

The module docstring promises that a pattern Pydantic cannot compile is
preserved in the type metadata as `x-unsupported-pattern`. That held for a plain
string; a string whose `format` maps to another Python type took a different
return path and lost the marker.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import json_schema_to_type

UNSUPPORTED = "(?!x)"


def _type(schema: dict[str, Any]) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return json_schema_to_type(schema)


def _json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return TypeAdapter(_type(schema)).json_schema()


def test_pattern_marker_without_a_format() -> None:
    assert (
        _json_schema({"type": "string", "pattern": UNSUPPORTED})[
            "x-unsupported-pattern"
        ]
        == UNSUPPORTED
    )


def test_pattern_marker_with_a_string_format() -> None:
    assert (
        _json_schema({"type": "string", "format": "uuid", "pattern": UNSUPPORTED})[
            "x-unsupported-pattern"
        ]
        == UNSUPPORTED
    )


def test_pattern_marker_with_a_non_string_format() -> None:
    assert (
        _json_schema({"type": "string", "format": "date-time", "pattern": UNSUPPORTED})[
            "x-unsupported-pattern"
        ]
        == UNSUPPORTED
    )


def test_supported_pattern_leaves_no_marker() -> None:
    schema = _json_schema(
        {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]+$"}
    )

    assert "x-unsupported-pattern" not in schema


def test_dropping_the_pattern_keeps_the_other_constraints() -> None:
    schema = {
        "type": "string",
        "format": "uuid",
        "minLength": 40,
        "pattern": UNSUPPORTED,
    }

    with pytest.raises(ValidationError):
        TypeAdapter(_type(schema)).validate_python(
            "2f1a4e6c-0f4d-4b7a-9c2e-8d1b3a5c7e9f"
        )


def test_format_with_unsupported_pattern_keeps_type_identity() -> None:
    """Repeated conversions return the same type.

    `_constrain_raw_string` is cached so a schema seen on every call maps to one
    type; wrapping its result in an uncached `Annotated[..., Field(...)]` broke
    that identity for the `format` route.
    """
    schema = {"type": "string", "format": "uuid", "pattern": "(?!x)"}

    first = json_schema_to_type(schema)
    second = json_schema_to_type(schema)

    assert first is second
