from fastmcp.utilities.json_schema import _prune_param, prune_params


def test_prune_param_nonexistent():
    """Test pruning a parameter that doesn't exist."""
    schema = {"properties": {"foo": {"type": "string"}}}
    result = _prune_param(schema, "bar")
    assert result == schema  # Schema should be unchanged


def test_prune_param_exists():
    """Test pruning a parameter that exists."""
    schema = {"properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}}}
    result = _prune_param(schema, "bar")
    assert result["properties"] == {"foo": {"type": "string"}}


def test_prune_param_last_property():
    """Test pruning the only/last parameter, should leave empty properties object."""
    schema = {"properties": {"foo": {"type": "string"}}}
    result = _prune_param(schema, "foo")
    assert "properties" in result
    assert result["properties"] == {}


def test_prune_param_from_required():
    """Test pruning a parameter that's in the required list."""
    schema = {
        "properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}},
        "required": ["foo", "bar"],
    }
    result = _prune_param(schema, "bar")
    assert result["required"] == ["foo"]


def test_prune_param_last_required():
    """Test pruning the last required parameter, should remove required field."""
    schema = {
        "properties": {"foo": {"type": "string"}, "bar": {"type": "integer"}},
        "required": ["foo"],
    }
    result = _prune_param(schema, "foo")
    assert "required" not in result


def test_prune_param_with_refs():
    """Test pruning a parameter that has references in $defs."""
    schema = {
        "properties": {
            "foo": {"$ref": "#/$defs/foo_def"},
            "bar": {"$ref": "#/$defs/bar_def"},
        },
        "$defs": {
            "foo_def": {"type": "string"},
            "bar_def": {"type": "integer"},
        },
    }
    result = _prune_param(schema, "bar")
    assert "bar_def" not in result["$defs"]
    assert "foo_def" in result["$defs"]


def test_prune_param_all_refs():
    """Test pruning all parameters with refs, should remove $defs."""
    schema = {
        "properties": {
            "foo": {"$ref": "#/$defs/foo_def"},
        },
        "$defs": {
            "foo_def": {"type": "string"},
        },
    }
    result = _prune_param(schema, "foo")
    assert "$defs" not in result


def test_prune_params_multiple():
    """Test pruning multiple parameters at once."""
    schema = {
        "properties": {
            "foo": {"type": "string"},
            "bar": {"type": "integer"},
            "baz": {"type": "boolean"},
        },
        "required": ["foo", "bar"],
    }
    result = prune_params(schema, ["foo", "baz"])
    assert result["properties"] == {"bar": {"type": "integer"}}
    assert result["required"] == ["bar"]


def test_prune_params_nested_refs():
    """Test pruning with nested references."""
    schema = {
        "properties": {
            "foo": {
                "type": "object",
                "properties": {"nested": {"$ref": "#/$defs/nested_def"}},
            },
            "bar": {"$ref": "#/$defs/bar_def"},
        },
        "$defs": {
            "nested_def": {"type": "string"},
            "bar_def": {"type": "integer"},
        },
    }
    # Removing foo should keep nested_def as it's not referenced anymore
    result = _prune_param(schema, "foo")
    assert "nested_def" not in result["$defs"]
    assert "bar_def" in result["$defs"]
