"""Generated parameter help must preserve descriptions as Python string literals."""

from inspect import get_annotations
from typing import Annotated, Any

import pytest
from pydantic import Field

from fastmcp import Client, FastMCP
from fastmcp.cli.generate import generate_cli_script


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
async def test_parameter_help_preserves_line_endings(newline: str) -> None:
    description = f"First \"line\"{newline}Second 'line' at C:\\tools — 설명"
    server = FastMCP("Descriptions")
    received: list[tuple[str, dict[str, str], str, dict[str, str] | None]] = []

    @server.tool
    def record(
        message: Annotated[str, Field(description=description)],
        options: Annotated[dict[str, str], Field(description=description)] = {"x": "y"},
        label: Annotated[str, Field(description=description)] = "default",
        optional: Annotated[
            dict[str, str] | None, Field(description=description)
        ] = None,
    ) -> None:
        received.append((message, options, label, optional))

    async with Client(server) as client:
        tools = await client.list_tools()
    script = generate_cli_script(
        server_name="descriptions",
        server_spec="descriptions",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server
    app = namespace["app"]
    # Python 3.10's get_type_hints adds Optional around parameters defaulted to None.
    annotations = get_annotations(app["call-tool"]["record"].default_command)
    for name in ("message", "label"):
        assert annotations[name].__metadata__[0].help == description
    for name in ("options", "optional"):
        assert annotations[name].__metadata__[0].help.startswith(description)

    await app.run_async(
        ["call-tool", "record", "--message", "hello"], result_action="return_value"
    )
    await app.run_async(
        [
            "call-tool",
            "record",
            "--message",
            "hi",
            "--options",
            '{"a": "b"}',
            "--label",
            "override",
            "--optional",
            '{"z": "q"}',
        ],
        result_action="return_value",
    )
    assert received == [
        ("hello", {"x": "y"}, "default", None),
        ("hi", {"a": "b"}, "override", {"z": "q"}),
    ]


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
async def test_parameter_help_preserves_explicit_flags(newline: str) -> None:
    description = f"First \"line\"{newline}Second 'line' at C:\\tools — 설명"
    server = FastMCP("Flag descriptions")
    received: list[tuple[str, str, dict[str, str], str, dict[str, str] | None]] = []

    @server.tool(name="app")
    def record(
        user_id: str,
        userId: Annotated[str, Field(description=description)],
        meta_data: dict[str, str] = {"x": "y"},
        metaData: Annotated[dict[str, str], Field(description=description)] = {
            "x": "y"
        },
        option_name: str = "default",
        optionName: Annotated[str, Field(description=description)] = "default",
        extra_data: dict[str, str] | None = None,
        extraData: Annotated[
            dict[str, str] | None, Field(description=description)
        ] = None,
    ) -> None:
        received.append((user_id, userId, metaData, optionName, extraData))

    async with Client(server) as client:
        tools = await client.list_tools()
    script = generate_cli_script(
        server_name="Flag descriptions",
        server_spec="descriptions",
        transport_code=repr("http://unused/mcp"),
        extra_imports=set(),
        tools=tools,
    )
    namespace: dict[str, Any] = {"__name__": "generated_cli"}
    exec(compile(script, "<generated>", "exec"), namespace)
    namespace["CLIENT_SPEC"] = server
    app = namespace["app"]
    annotations = get_annotations(app["call-tool"]["app"].default_command)
    for name in ("userId", "optionName"):
        assert annotations[name].__metadata__[0].help == description
    for name in ("metaData", "extraData"):
        assert annotations[name].__metadata__[0].help.startswith(description)

    await app.run_async(
        ["call-tool", "app", "--user-id", "snake", "--userId", "camel"],
        result_action="return_value",
    )
    await app.run_async(
        [
            "call-tool",
            "app",
            "--user-id",
            "s",
            "--userId",
            "c",
            "--metaData",
            '{"a": "b"}',
            "--optionName",
            "override",
            "--extraData",
            '{"z": "q"}',
        ],
        result_action="return_value",
    )
    assert received == [
        ("snake", "camel", {"x": "y"}, "default", None),
        ("s", "c", {"a": "b"}, "override", {"z": "q"}),
    ]
