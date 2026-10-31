"""Behavioral tests for arguments passed by generated CLI commands."""

from typing import Any
from unittest.mock import AsyncMock

import mcp_types
import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import generate_cli_script


async def generated_cli(server: FastMCP) -> dict[str, Any]:
    async with Client(server) as client:
        tools = await client.list_tools()
    source = generate_cli_script(
        "profiles", "profiles", repr("http://unused/mcp"), set(), tools
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(source, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server
    return namespace


@pytest.mark.parametrize(
    ("value", "expected"),
    [("null", None), ("{}", {}), ('{"name":"Alice"}', {"name": "Alice"})],
)
async def test_required_json_parameter_preserves_null(
    value: str, expected: dict[str, str] | None
) -> None:
    server = FastMCP("Profiles")
    received: list[dict[str, str] | None] = []

    @server.tool
    def set_profile(profile: dict[str, str] | None) -> None:
        received.append(profile)

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "set_profile", "--profile", value],
        result_action="return_value",
    )

    assert received == [expected]


@pytest.mark.parametrize("use_default_factory", [False, True])
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], {"name": "default"}),
        (["--profile", "null"], None),
        (["--profile", "{}"], {}),
        (["--profile", '{"name":"Alice"}'], {"name": "Alice"}),
    ],
)
async def test_optional_json_parameter_distinguishes_null_from_omission(
    use_default_factory: bool,
    arguments: list[str],
    expected: dict[str, str] | None,
) -> None:
    server = FastMCP("Profiles")
    received: list[dict[str, str] | None] = []
    default = (
        Field(default_factory=lambda: {"name": "default"})
        if use_default_factory
        else {"name": "default"}
    )

    @server.tool
    def set_profile(profile: dict[str, str] | None = default) -> None:
        received.append(profile)

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "set_profile", *arguments], result_action="return_value"
    )

    assert received == [expected]


async def test_json_parameters_do_not_collide_with_argument_dictionary() -> None:
    server = FastMCP("Profiles")
    received: list[tuple[dict[str, str] | None, str]] = []

    @server.tool
    def set_profile(_arguments: dict[str, str] | None, _arguments_: str) -> None:
        received.append((_arguments, _arguments_))

    namespace = await generated_cli(server)
    await namespace["set_profile"](_arguments="null", _arguments_="keep")

    assert received == [(None, "keep")]


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


async def test_parameter_named_json_does_not_break_json_arguments() -> None:
    server = FastMCP("Requests")
    received: list[tuple[dict[str, int], dict[str, str]]] = []

    @server.tool
    def post(json: dict[str, int], headers: dict[str, str]) -> None:
        received.append((json, headers))

    async with Client(server) as client:
        tools = await client.list_tools()
    script = generate_cli_script(
        server_name="requests",
        server_spec="requests",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server

    await namespace["app"].run_async(
        ["call-tool", "post", "--json", '{"a": 1}', "--headers", '{"b": "2"}'],
        result_action="return_value",
    )

    assert received == [({"a": 1}, {"b": "2"})]
