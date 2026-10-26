"""Generated parameter help must preserve descriptions as Python string literals."""

from typing import Annotated, Any, get_type_hints

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
    annotations = get_type_hints(
        app["call-tool"]["record"].default_command, include_extras=True
    )
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
