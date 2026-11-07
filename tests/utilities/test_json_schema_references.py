import pytest

from fastmcp.utilities.json_schema import compress_schema


class TestDefinitionReferences:
    @pytest.mark.parametrize(
        ("definition_name", "ref"),
        [
            ("Name", "#/$defs/Name"),
            ("Name/part", "#/$defs/Name~1part"),
            ("Name~part", "#/$defs/Name~0part"),
            ("Name~1part", "#/$defs/Name~01part"),
            ("Name part", "#/$defs/Name%20part"),
            ("Name%2Fpart", "#/$defs/Name%252Fpart"),
            ("Name", "#/%24defs/Name"),
        ],
    )
    def test_keeps_encoded_definition_references(self, definition_name: str, ref: str):
        schema = {
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/Alias"}},
            "$defs": {
                "Alias": {"$ref": ref},
                definition_name: {"type": "string"},
                "Unused": {"type": "number"},
            },
        }

        result = compress_schema(schema)

        assert result["$defs"] == {
            "Alias": {"$ref": ref},
            definition_name: {"type": "string"},
        }
        assert result["properties"] == schema["properties"]

    @pytest.mark.parametrize(
        "ref",
        [
            "#/$defs/Envelope/properties/name",
            "#/$defs/Envelope%2Fproperties%2Fname",
        ],
    )
    def test_keeps_owner_of_nested_definition_reference(self, ref: str):
        envelope = {
            "type": "object",
            "properties": {"name": {"$ref": "#/$defs/Leaf"}},
        }
        schema = {
            "type": "object",
            "properties": {"value": {"$ref": ref}},
            "$defs": {
                "Envelope": envelope,
                "Leaf": {"type": "string"},
                "name": {"type": "number"},
            },
        }

        result = compress_schema(schema)

        assert result["$defs"] == {
            "Envelope": envelope,
            "Leaf": {"type": "string"},
        }
        assert result["properties"] == schema["properties"]
