"""Behavioral tests for arguments passed by generated CLI commands."""

from typing import Any
from unittest.mock import AsyncMock

import mcp_types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import (
    _tool_function_source,
    generate_cli_script,
    generate_skill_content,
)


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


async def generated_app(server: FastMCP) -> Any:
    async with Client(server) as client:
        tools = await client.list_tools()
    script = generate_cli_script(
        server_name="test",
        server_spec="test",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server
    return namespace["app"]


async def test_optional_string_parameter_takes_plain_text() -> None:
    server = FastMCP("Greeter")
    received: list[tuple[str, str | None]] = []

    @server.tool
    def greet(name: str, greeting: str | None = None) -> None:
        received.append((name, greeting))

    app = await generated_app(server)
    await app.run_async(
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

    app = await generated_app(server)
    await app.run_async(
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

    assert "value = json.loads(value) if isinstance(value, str) else value" in source
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
