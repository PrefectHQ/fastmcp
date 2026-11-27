"""Preserve reference resolution when an untyped schema has sibling properties."""

from datetime import date
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from fastmcp.utilities.json_schema_type import json_schema_to_type


@pytest.mark.parametrize("extra_schema", [None, False, {}])
@pytest.mark.parametrize("missing_required", [False, True])
def test_untyped_sibling_reference_preserves_definition(
    extra_schema: Any, missing_required: bool
) -> None:
    details: dict[str, Any] = {
        "$ref": "#/$defs/Record",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    if extra_schema is not None:
        details["additionalProperties"] = extra_schema
    schema = {
        "type": "object",
        "properties": {"details": details},
        "required": ["details"],
        "$defs": {
            "Record": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "recorded_at": {"type": "string", "format": "date"},
                },
                "required": ["name", "recorded_at"],
            }
        },
    }
    adapter = TypeAdapter(json_schema_to_type(schema))
    payload = {"details": {"name": "cache", "recorded_at": "2026-11-19"}}
    if missing_required:
        del payload["details"]["recorded_at"]
        with pytest.raises(ValidationError, match="recorded_at"):
            adapter.validate_python(payload)
    else:
        result = adapter.validate_python(payload)
        assert result.details.recorded_at == date(2026, 11, 19)
        assert type(result.details.recorded_at) is date
