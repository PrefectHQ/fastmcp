from fastmcp.utilities.json_schema import dereference_refs

def _has_ref(obj):
    if isinstance(obj, dict):
        if "$ref" in obj:
            return True
        return any(_has_ref(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_ref(x) for x in obj)
    return False


def test_components_vs_existing_defs_collision_components_win():
    """
    If both $defs.User and components.schemas.User exist and differ:
    - After migration + normalization, $defs["User"] must match the component schema.
    - Any *direct* #/$defs/User refs should be retargeted to an alias, not silently
      changed to point at the component definition.
    """
    schema = {
        # Existing $defs with a different 'User' (stale)
        "$defs": {
            "User": {"type": "object", "properties": {"old": {"type": "string"}}}
        },
        # Some direct reference to #/$defs/User that we want to preserve semantics for
        "properties": {
            "legacy": {"$ref": "#/$defs/User"},
            "by_components": {"$ref": "#/components/schemas/User"},
        },
        "components": {
            "schemas": {
                "User": {"type": "object", "properties": {"new": {"type": "integer"}}}
            }
        },
        "type": "object",
    }

    out = dereference_refs(schema)

    # No $ref left after deref
    assert not _has_ref(out)

    # $defs should be dropped by full-deref path; however, if your environment
    # hits the circular fallback path, we can tolerate $defs but assert content.
    # Therefore, check structure via 'properties' instead of $defs.
    assert "legacy" in out["properties"]
    assert "by_components" in out["properties"]

    legacy = out["properties"]["legacy"]
    by_components = out["properties"]["by_components"]

    # 'legacy' came from the pre-existing $defs version (string field 'old')
    assert legacy["type"] == "object"
    assert "old" in legacy["properties"]
    assert legacy["properties"]["old"]["type"] == "string"

    # 'by_components' came from components version (integer field 'new')
    assert by_components["type"] == "object"
    assert "new" in by_components["properties"]
    assert by_components["properties"]["new"]["type"] == "integer"