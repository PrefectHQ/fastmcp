import copy
from fastmcp.utilities.json_schema import dereference_refs

def _contains_ref(obj):
    """
    Return True if ANY $ref exists anywhere in the nested structure.
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            return True
        return any(_contains_ref(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_ref(x) for x in obj)
    return False


def test_propertynames_and_additionalproperties_refs_are_normalized_and_dereferenced():
    # This is the minimal schema reproducing the problem:
    # - propertyNames.$ref -> Category  (enum keys)
    # - additionalProperties.$ref -> ItemInfo (values)
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "object",
                "additionalProperties": {"$ref": "#/components/schemas/ItemInfo"},
                "propertyNames": {"$ref": "#/components/schemas/Category"},
            }
        },
        "components": {
            "schemas": {
                "Category": {
                    "type": "string",
                    "enum": ["Books", "Electronics"]
                },
                "ItemInfo": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"}
                    }
                }
            }
        }
    }

    # Apply your normalization + dereference logic
    out = dereference_refs(copy.deepcopy(schema))

    # After dereferencing:
    #   1) There should be NO $defs
    #   2) There should be NO $ref anywhere
    assert "$defs" not in out, f"Unexpected $defs left: {out.get('$defs')}"
    assert not _contains_ref(out), f"Found unexpected $ref in:\n{out}"

    # Sanity check: items is still an object
    items = out["properties"]["items"]
    assert items["type"] == "object"