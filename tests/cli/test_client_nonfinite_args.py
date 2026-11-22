"""NaN/Infinity CLI arguments must be rejected at parse time: the JSON-RPC
layer serializes them to null, so the server silently receives None instead
of the typed value. Mirrors the tool-result guard in tools/base.py. See #6674.
"""

import pytest

from fastmcp.cli.client import parse_tool_arguments

NUMBER_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "number"}},
}


def test_nan_key_value_arg_is_rejected(capsys):
    with pytest.raises(SystemExit):
        parse_tool_arguments(("x=nan",), None, NUMBER_SCHEMA)
    assert "cannot represent nan" in capsys.readouterr().out


def test_inf_key_value_arg_is_rejected(capsys):
    with pytest.raises(SystemExit):
        parse_tool_arguments(("x=inf",), None, NUMBER_SCHEMA)
    assert "cannot represent inf" in capsys.readouterr().out


def test_nan_in_input_json_is_rejected(capsys):
    with pytest.raises(SystemExit):
        parse_tool_arguments((), '{"x": NaN}', NUMBER_SCHEMA)
    assert "cannot represent nan" in capsys.readouterr().out


def test_overflow_to_infinity_in_input_json_is_rejected(capsys):
    with pytest.raises(SystemExit):
        parse_tool_arguments((), '{"x": 1e400}', NUMBER_SCHEMA)
    assert "cannot represent inf" in capsys.readouterr().out


def test_nan_inside_array_is_rejected(capsys):
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "number"}}},
    }
    with pytest.raises(SystemExit):
        parse_tool_arguments(("items=[1, NaN]",), None, schema)
    assert "cannot represent nan" in capsys.readouterr().out


def test_finite_values_pass_through() -> None:
    schema = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "array", "items": {"type": "number"}},
        },
    }
    result = parse_tool_arguments(("x=3.14",), '{"y": [1, 2]}', schema)
    assert result == {"x": 3.14, "y": [1, 2]}
