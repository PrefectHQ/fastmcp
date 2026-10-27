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


async def test_optional_string_parameter_takes_plain_text() -> None:
    server = FastMCP("Greeter")
    received: list[tuple[str, str | None]] = []

    @server.tool
    def greet(name: str, greeting: str | None = None) -> None:
        received.append((name, greeting))

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "greet", "--name", "World", "--greeting", "Howdy"],
        result_action="return_value",
    )

    assert received == [("World", "Howdy")]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], (None, None, None)),
        (["--count", "3", "--loud", "--labels", '{"a": 1}'], (3, True, {"a": 1})),
    ],
)
async def test_optional_parameters_keep_their_types(
    arguments: list[str],
    expected: tuple[int | None, bool | None, dict[str, int] | None],
) -> None:
    server = FastMCP("Options")
    received: list[tuple[int | None, bool | None, dict[str, int] | None]] = []

    @server.tool
    def configure(
        count: int | None = None,
        loud: bool | None = None,
        labels: dict[str, int] | None = None,
    ) -> None:
        received.append((count, loud, labels))

    namespace = await generated_cli(server)
    await namespace["app"].run_async(
        ["call-tool", "configure", *arguments], result_action="return_value"
    )

    assert received == [expected]
    assert [type(value) for value in received[0]] == [type(value) for value in expected]


@pytest.mark.parametrize(
    ("value_schema", "annotation"),
    [
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "str | None"),
        (
            {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
            "int | None",
        ),
        (
            {"anyOf": [{"type": ["string", "integer"]}, {"type": "boolean"}]},
            "str | int | bool | None",
        ),
    ],
)
def test_optional_simple_any_of_parameter_is_a_typed_flag(
    value_schema: dict[str, Any], annotation: str
) -> None:
    tool = mcp_types.Tool(
        name="set",
        input_schema={"properties": {"value": {**value_schema, "default": None}}},
    )
    source = _tool_function_source(tool)

    assert f"value: Annotated[{annotation}, cyclopts.Parameter(" in source
    assert "JSON Schema" not in source
    assert "json.loads" not in source
    compile(source, "<test>", "exec")


@pytest.mark.parametrize(
    "value_schema",
    [
        {"anyOf": [{"type": "object"}, {"type": "null"}]},
        {"anyOf": [True, {"type": "string"}]},
    ],
)
def test_any_of_with_complex_branch_is_parsed_as_json(
    value_schema: dict[str, Any],
) -> None:
    tool = mcp_types.Tool(
        name="set",
        input_schema={"properties": {"value": value_schema}, "required": ["value"]},
    )
    source = _tool_function_source(tool)

    assert "_arguments['value'] = json.loads(_arguments['value'])" in source
    compile(source, "<test>", "exec")


def test_skill_documents_optional_any_of_parameters_as_typed_flags() -> None:
    tool = mcp_types.Tool(
        name="greet",
        input_schema={
            "type": "object",
            "properties": {
                "greeting": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
                "loud": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "default": None,
                },
            },
        },
    )
    content = generate_skill_content("test", "cli.py", [tool])

    assert "--greeting <value>" in content
    assert "--loud <value>" not in content
    assert "| `--greeting` | string \\| null | no |" in content
    assert "JSON string" not in content
