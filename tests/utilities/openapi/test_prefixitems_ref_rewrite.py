from copy import deepcopy

from fastmcp.utilities.openapi.schemas import _replace_ref_with_defs


def test_replace_ref_with_defs_rewrites_nested_prefix_items() -> None:
    schema = {
        "type": "array",
        "prefixItems": [
            {"$ref": "#/components/schemas/Item"},
            {
                "type": "array",
                "prefixItems": [{"$ref": "#/components/schemas/Nested"}],
            },
        ],
        "items": {"$ref": "#/components/schemas/Tail"},
        "examples": [[{"$ref": "#/components/schemas/Example"}]],
    }
    original = deepcopy(schema)

    result = _replace_ref_with_defs(schema)

    assert result["prefixItems"][0] == {"$ref": "#/$defs/Item"}
    assert result["prefixItems"][1]["prefixItems"] == [{"$ref": "#/$defs/Nested"}]
    assert result["items"] == {"$ref": "#/$defs/Tail"}
    assert result["examples"] == original["examples"]
    assert schema == original


def test_replace_ref_with_defs_preserves_boolean_prefix_items() -> None:
    schema = {
        "type": "array",
        "prefixItems": [True, {"$ref": "#/components/schemas/Item"}, False],
    }

    assert _replace_ref_with_defs(schema) == {
        "type": "array",
        "prefixItems": [True, {"$ref": "#/$defs/Item"}, False],
    }
