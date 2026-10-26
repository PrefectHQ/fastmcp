"""Behavioral tests for arguments passed by generated CLI commands."""

from typing import Any
from unittest.mock import AsyncMock

import mcp_types
import pytest

from fastmcp.cli.generate import generate_cli_script


@pytest.mark.parametrize("parsed_type", ["string", "object"])
@pytest.mark.parametrize("use_default", [False, True])
async def test_complex_parameter_preserves_parsed_suffix_parameter(
    parsed_type: str, use_default: bool
) -> None:
    metadata_schema: dict[str, Any] = {"type": "object"}
    required = ["metadata_parsed"]
    if use_default:
        metadata_schema["default"] = {"role": "admin"}
    else:
        required.append("metadata")
    tool = mcp_types.Tool(
        name="create_user",
        input_schema={
            "type": "object",
            "properties": {
                "metadata": metadata_schema,
                "metadata_parsed": {"type": parsed_type},
            },
            "required": required,
        },
    )
    script = generate_cli_script(
        server_name="test",
        server_spec="test",
        transport_code="'http://localhost:8000/mcp'",
        extra_imports=set(),
        tools=[tool],
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    call_tool = AsyncMock()
    namespace["_call_tool"] = call_tool

    parsed_value = "keep" if parsed_type == "string" else '{"dept": "engineering"}'
    arguments = {"metadata_parsed": parsed_value}
    if not use_default:
        arguments["metadata"] = '{"role": "admin"}'
    await namespace["create_user"](**arguments)

    call_tool.assert_awaited_once_with(
        "create_user",
        {
            "metadata": {"role": "admin"},
            "metadata_parsed": (
                "keep" if parsed_type == "string" else {"dept": "engineering"}
            ),
        },
    )
