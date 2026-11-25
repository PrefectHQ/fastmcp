"""Behavioral tests for arguments passed by generated CLI commands."""

from typing import Any
from unittest.mock import AsyncMock

import mcp_types
import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import (
    _tool_function_source,
    generate_cli_script,
    generate_skill_content,
)


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
    await namespace["tool_set_profile"](_arguments="null", _arguments_="keep")

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
    await namespace["tool_create_user"](**arguments)

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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Howdy", "Howdy"),
        ('"Howdy"', "Howdy"),
        ("null", None),
        ('"null"', "null"),
        ("NULL", "NULL"),
        ("123", "123"),
        ("true", "true"),
        ('{"a": 1}', '{"a": 1}'),
        ("", ""),
    ],
)
async def test_optional_string_parameter_takes_plain_text(
    value: str, expected: str | None
) -> None:
    server = FastMCP("Greeter")
    received: list[str | None] = []

    @server.tool
    def greet(greeting: str | None = None) -> None:
        received.append(greeting)

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "greet", "--greeting", value], result_action="return_value"
    )

    assert received == [expected]
    assert type(received[0]) is type(expected)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ("Hello", "null", "Pal")),
        (
            ["--greeting", "null", "--fallback", "null", "--nickname", "null"],
            (None, None, None),
        ),
        (
            ["--greeting", "Howdy", "--fallback", '"null"', "--nickname", "Bob"],
            ("Howdy", "null", "Bob"),
        ),
    ],
)
async def test_optional_string_parameter_distinguishes_null_from_omission(
    arguments: list[str], expected: tuple[str | None, str | None, str | None]
) -> None:
    server = FastMCP("Greeter")
    received: list[tuple[str | None, str | None, str | None]] = []

    @server.tool
    def greet(
        greeting: str | None = "Hello",
        fallback: str | None = "null",
        nickname: str | None = Field(default_factory=lambda: "Pal"),
    ) -> None:
        received.append((greeting, fallback, nickname))

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "greet", *arguments], result_action="return_value"
    )

    assert received == [expected]
    assert [type(value) for value in received[0]] == [type(value) for value in expected]


@pytest.mark.parametrize(
    ("tool_name", "value", "expected"),
    [
        ("str_or_int", "3", 3),
        ("str_or_int", '"3"', "3"),
        ("int_or_bool", "3", 3),
        ("int_or_bool", "true", True),
        ("int_or_none", "7", 7),
        ("int_or_none", "null", None),
        ("bool_or_none", "false", False),
        ("bool_or_none", "null", None),
    ],
)
async def test_other_unions_take_json_values(
    tool_name: str, value: str, expected: str | int | bool | None
) -> None:
    server = FastMCP("Unions")
    received: list[str | int | bool | None] = []

    @server.tool
    def str_or_int(value: str | int) -> None:
        received.append(value)

    @server.tool
    def int_or_bool(value: int | bool) -> None:
        received.append(value)

    @server.tool
    def int_or_none(value: int | None = 1) -> None:
        received.append(value)

    @server.tool
    def bool_or_none(value: bool | None = True) -> None:
        received.append(value)

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", tool_name, "--value", value], result_action="return_value"
    )

    assert received == [expected]
    assert type(received[0]) is type(expected)


@pytest.mark.parametrize(
    "value_schema",
    [
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        {"anyOf": [{"type": "null"}, {"type": "string", "maxLength": 10}]},
    ],
)
def test_optional_string_any_of_parameter_is_a_plain_flag(
    value_schema: dict[str, Any],
) -> None:
    tool = mcp_types.Tool(
        name="set",
        input_schema={"properties": {"value": {**value_schema, "default": None}}},
    )
    source = _tool_function_source(tool)

    assert "value: Annotated[str | None, cyclopts.Parameter(help='')] = None" in source
    assert "_arguments['value'] = _json_text(_arguments['value'])" in source
    assert "json.loads" not in source
    compile(source, "<test>", "exec")


@pytest.mark.parametrize(
    "value_schema",
    [
        {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        {"anyOf": [{"type": "number"}, {"type": "null"}]},
        {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        {"anyOf": [{"type": "object"}, {"type": "null"}]},
        {"anyOf": [{"type": "string", "enum": ["red", "green"]}, {"type": "null"}]},
        {"anyOf": [{"type": "string", "const": "red"}, {"type": "null"}]},
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
        {"anyOf": [True, {"type": "null"}]},
    ],
)
def test_other_any_of_unions_are_parsed_as_json(
    value_schema: dict[str, Any],
) -> None:
    tool = mcp_types.Tool(
        name="set",
        input_schema={"properties": {"value": value_schema}, "required": ["value"]},
    )
    source = _tool_function_source(tool)

    assert "JSON Schema:" in source
    assert "_arguments['value'] = json.loads(_arguments['value'])" in source
    compile(source, "<test>", "exec")


async def test_parameter_named_json_text_does_not_break_optional_strings() -> None:
    server = FastMCP("Notes")
    received: list[tuple[str, str | None]] = []

    @server.tool
    def note(_json_text: str, title: str | None = None) -> None:
        received.append((_json_text, title))

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "note", "--json-text", "body", "--title", '"Hi"'],
        result_action="return_value",
    )

    assert received == [("body", "Hi")]


def test_skill_documents_optional_strings_as_plain_flags() -> None:
    tool = mcp_types.Tool(
        name="greet",
        input_schema={
            "type": "object",
            "properties": {
                "greeting": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
                "count": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                },
            },
        },
    )
    content = generate_skill_content("test", "cli.py", [tool])

    assert "| `--greeting` | string \\| null | no |  |" in content
    assert "| `--count` | string | no | JSON string |" in content
