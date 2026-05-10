from fastmcp.utilities.openapi.schemas import _replace_ref_with_defs


def test_replace_ref_with_defs_rewrites_propertyNames_ref():
    """
    Regression test for issue #3303.

    When using dict[StrEnum, Model], Pydantic generates:

        {
            "type": "object",
            "propertyNames": {"$ref": "#/components/schemas/Category"},
            "additionalProperties": {"$ref": "#/components/schemas/ItemInfo"}
        }

    _replace_ref_with_defs should rewrite BOTH refs to #/$defs/.
    """

    schema = {
        "type": "object",
        "propertyNames": {"$ref": "#/components/schemas/Category"},
        "additionalProperties": {"$ref": "#/components/schemas/ItemInfo"},
    }

    result = _replace_ref_with_defs(schema)

    # additionalProperties ref is rewritten
    assert result["additionalProperties"]["$ref"] == "#/$defs/ItemInfo"

    # propertyNames ref must also be rewritten (this was the bug)
    assert result["propertyNames"]["$ref"] == "#/$defs/Category"

    # Ensure no dangling OpenAPI refs remain
    assert "#/components/schemas/" not in str(result)
