"""Test for fix of RecursionError on self-referential $ref in anyOf.

Regression test for https://github.com/PrefectHQ/fastmcp/issues/4306
"""

import pytest
from pydantic import TypeAdapter

from fastmcp.utilities.json_schema_type import json_schema_to_type


class TestRecursiveRef:
    """Test suite for recursive/self-referential JSON Schema $ref handling."""

    def test_anyof_self_referential_ref_no_recursion_error(self):
        """json_schema_to_type should not RecursionError on self-referential $ref in anyOf."""
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/JSONValue"}},
            },
            "$defs": {
                "JSONValue": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"$ref": "#/$defs/JSONValue"},
                        },
                        {
                            "type": "object",
                            "additionalProperties": {"$ref": "#/$defs/JSONValue"},
                        },
                    ]
                }
            },
        }
        # Should not raise RecursionError
        result = json_schema_to_type(schema)
        assert result is not None

    def test_anyof_self_referential_ref_validates(self):
        """The returned type should validate correct JSON values."""
        schema = {
            "type": "object",
            "properties": {
                "data": {"$ref": "#/$defs/JSONValue"},
            },
            "$defs": {
                "JSONValue": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "null"},
                    ]
                }
            },
        }
        result = json_schema_to_type(schema)
        ta = TypeAdapter(result)
        # Should validate without error
        validated = ta.validate_python({"data": "hello"})
        assert validated.data == "hello"
        validated = ta.validate_python({"data": 42})
        assert validated.data == 42
        validated = ta.validate_python({"data": None})
        assert validated.data is None

    def test_nested_object_self_ref_no_recursion(self):
        """Self-referential object with additionalProperties should not recurse."""
        schema = {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Node"},
                        },
                        "name": {"type": "string"},
                    },
                }
            },
            "properties": {
                "root": {"$ref": "#/$defs/Node"},
            },
        }
        result = json_schema_to_type(schema)
        assert result is not None
