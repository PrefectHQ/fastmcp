from fastmcp.utilities.openapi.json_schema_converter import (
    convert_openapi_schema_to_json_schema,
)


def test_discriminator_property_remains_required_when_removed():
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "comprehensive", "type": "string"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "validate", "type": "string"},
                    "target_id": {"type": "string"},
                },
                "required": ["target_id"],
            },
        ],
        "discriminator": {"propertyName": "kind"},
    }

    result = convert_openapi_schema_to_json_schema(schema, "3.0.0")

    assert "oneOf" not in result
    assert "discriminator" not in result
    for variant in result["anyOf"]:
        assert "kind" in variant["required"]
    validate_schema = next(
        variant
        for variant in result["anyOf"]
        if variant["properties"]["kind"]["const"] == "validate"
    )
    assert validate_schema["required"] == ["target_id", "kind"]
