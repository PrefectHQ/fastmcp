"""Windows --command parsing at the public server-resolution boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fastmcp.cli.client import resolve_server_spec
from fastmcp.client.transports import StdioTransport

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows argv parsing"
)


@pytest.mark.parametrize(
    "argv",
    [
        [r"C:\python\python.exe", r"C:\mcp\server.py"],
        [r"C:\Program Files\Python\python.exe", r"C:\project dir\server.py"],
        ["server", r"--config=C:\Program Files\mcp.json"],
        ["server", "", "two words", 'a"b', "C:\\trailing\\"],
        ["server", "C:\\space dir\\", "中文路径"],
    ],
)
def test_command_roundtrips_windows_argv(argv: list[str]) -> None:
    transport = resolve_server_spec(None, command=subprocess.list2cmdline(argv))
    assert isinstance(transport, StdioTransport)
    assert [transport.command, *transport.args] == argv


def test_embedded_option_quotes() -> None:
    transport = resolve_server_spec(
        None, command=r'server --config="C:\Program Files\mcp.json"'
    )
    assert isinstance(transport, StdioTransport)
    assert transport.args == [r"--config=C:\Program Files\mcp.json"]


@given(st.lists(st.text(alphabet='abc XYZ\\"\t中文', max_size=25), max_size=8))
def test_windows_argument_roundtrip(arguments: list[str]) -> None:
    argv = ["server.exe", *arguments]
    transport = resolve_server_spec(None, command=subprocess.list2cmdline(argv))
    assert isinstance(transport, StdioTransport)
    assert [transport.command, *transport.args] == argv


def test_launch_preserves_arguments(tmp_path: Path) -> None:
    script = tmp_path / "echo args 中文.py"
    script.write_text(
        "import sys, json; print(json.dumps(sys.argv[1:]))", encoding="utf-8"
    )
    arguments = [
        r"--config=C:\Program Files\mcp.json",
        "",
        'quote"value',
        "C:\\space dir\\",
    ]
    transport = resolve_server_spec(
        None, command=subprocess.list2cmdline([sys.executable, str(script), *arguments])
    )
    assert isinstance(transport, StdioTransport)
    result = subprocess.run(
        [transport.command, *transport.args], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == arguments


@pytest.mark.parametrize("command", ["", "   ", "server\0arg"])
def test_invalid_or_empty_command_exits(command: str) -> None:
    with pytest.raises(SystemExit):
        resolve_server_spec(None, command=command)


def test_leading_space_is_ignored() -> None:
    transport = resolve_server_spec(None, command="   server.exe arg")
    assert isinstance(transport, StdioTransport)
    assert transport.command == "server.exe"
    assert transport.args == ["arg"]
