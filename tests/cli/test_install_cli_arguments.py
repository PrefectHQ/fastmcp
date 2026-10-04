"""Client CLI argument handling for `fastmcp install claude-code|gemini-cli`.

On Windows, npm installs these CLIs as `.cmd` wrappers that run through
cmd.exe. The tests below check that each argument reaches the CLI literally.
"""

import json
import subprocess
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest

from fastmcp.cli.install import claude_code, gemini_cli, shared
from fastmcp.cli.install.shared import (
    quote_windows_batch_argument,
    run_cli_command,
    windows_batch_command_line,
)
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment

CMD_PREFIX = 'cmd.exe /e:on /v:off /d /s /c "'
CMD_OPERATORS = frozenset("&|<>()")
FAKE_CMD_ENVIRONMENT = {
    "cd": "C:\\work",
    "path": "C:\\Windows\\System32",
    "x": "value",
}

LITERAL_ARGUMENTS = [
    "pandas>=2.0",
    "numpy!=1.0",
    'foo[extra]>=1,<2; python_version<"3.12"',
    "C:\\Program Files (x86)\\x",
    "https://example.com/pkg.whl?a=1&b=2#sha256=abc",
    "100%",
    "%PATH%",
    "%PATH:Windows=Win%",
    "%%",
    "%x%x%",
    "%cd:~,%",
    "!x!",
    "^caret^",
    "a|b",
    'say "hi"',
    '"',
    '""',
    "",
    " ",
    "C:\\dir\\",
    'C:\\dir\\"quoted"\\',
    'back\\\\"slash',
    "API_URL=https://example.com/api?user=me&page=2",
    "SEARCH=cats|dogs",
    "PATTERN=^[a-z]+$",
    'GREETING=say "hi" & wave',
    'CONFIG={"key": "value", "ratio": "50%"}',
    "NOTE=it's done; ok, thanks = yes",
    "café ünïcode",
    "tab\tseparated",
]


def expand_percent_command_line(text: str) -> str:
    """Model cmd.exe percent expansion for a `cmd /c` command line.

    An undefined `%name%` is kept and scanning resumes at its closing `%`.
    `%cd:~,%` expands to an empty string. Any other expansion of a defined
    variable inserts that variable's value.
    """
    expanded: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "%":
            expanded.append(text[index])
            index += 1
            continue
        end = text.find("%", index + 1)
        if end == -1:
            expanded.append(text[index:])
            break
        name, _, modifier = text[index + 1 : end].partition(":")
        value = FAKE_CMD_ENVIRONMENT.get(name.lower())
        if value is None:
            expanded.append("%")
            index = end
            continue
        expanded.append("" if modifier == "~," else value)
        index = end + 1
    return "".join(expanded)


def parse_cmd_special_characters(text: str) -> str:
    """Model cmd.exe quote and caret handling, failing on unquoted operators."""
    parsed: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            quoted = not quoted
            parsed.append(char)
        elif not quoted and char == "^":
            index += 1
            parsed.append(text[index : index + 1])
        elif not quoted and char in CMD_OPERATORS:
            raise AssertionError(f"cmd.exe would treat {char!r} as an operator")
        else:
            parsed.append(char)
        index += 1
    return "".join(parsed)


def split_c_runtime_arguments(text: str) -> list[str]:
    """Split a command line the way the Microsoft C runtime builds argv."""
    arguments: list[str] = []
    index, length = 0, len(text)
    while True:
        while index < length and text[index] in " \t":
            index += 1
        if index == length:
            return arguments
        current: list[str] = []
        quoted = False
        while index < length:
            char = text[index]
            if char == "\\":
                start = index
                while index < length and text[index] == "\\":
                    index += 1
                count = index - start
                if index < length and text[index] == '"':
                    current.append("\\" * (count // 2))
                    if count % 2:
                        current.append('"')
                        index += 1
                else:
                    current.append("\\" * count)
                continue
            if char == '"':
                if quoted and text[index + 1 : index + 2] == '"':
                    current.append('"')
                    index += 2
                else:
                    quoted = not quoted
                    index += 1
                continue
            if char in " \t" and not quoted:
                break
            current.append(char)
            index += 1
        arguments.append("".join(current))


def arguments_received_through_batch_file(command_line: str) -> tuple[str, list[str]]:
    """Return the batch file and the argv its program receives.

    Models `cmd /s /c`, then a batch file line such as npm's
    `"%_prog%" "%dp0%\\cli.js" %*`, which parses the expanded `%*` again.
    """
    assert command_line.startswith(CMD_PREFIX)
    assert command_line.endswith('"')
    inner = command_line[len(CMD_PREFIX) : -1]
    after_cmd = parse_cmd_special_characters(expand_percent_command_line(inner))
    assert after_cmd.startswith('"')
    script_end = after_cmd.index('"', 1) + 1
    script = after_cmd[1 : script_end - 1]
    batch_line = '"node.exe" "cli.js"' + after_cmd[script_end:]
    received = split_c_runtime_arguments(parse_cmd_special_characters(batch_line))
    assert received[:2] == ["node.exe", "cli.js"]
    return script, received[2:]


class TestQuoteWindowsBatchArgument:
    @pytest.mark.parametrize(
        "argument,expected",
        [
            ("mcp", "mcp"),
            ("--", "--"),
            ("C:\\venv\\Scripts\\uv.exe", "C:\\venv\\Scripts\\uv.exe"),
            ("café", "café"),
            ("", '""'),
            ("a b", '"a b"'),
            ("pandas>=2.0", '"pandas>=2.0"'),
            ("C:\\Program Files (x86)\\x", '"C:\\Program Files (x86)\\x"'),
            ('say "hi"', '"say ""hi"""'),
            ('a\\"b', '"a\\\\""b"'),
            ("C:\\dir\\", '"C:\\dir\\\\"'),
            ("100%", '"100%%cd:~,%"'),
            ("!x!", '"!x!"'),
            ("a&b", '"a&b"'),
        ],
    )
    def test_quoted_form(self, argument: str, expected: str):
        assert quote_windows_batch_argument(argument) == expected

    @pytest.mark.parametrize("argument", ["a\nb", "a\rb", "value\r\n"])
    def test_rejects_line_breaks(self, argument: str):
        with pytest.raises(ValueError, match="line breaks"):
            quote_windows_batch_argument(argument)


class TestWindowsBatchCommandLine:
    @pytest.mark.parametrize("argument", LITERAL_ARGUMENTS)
    def test_argument_arrives_literally(self, argument: str):
        command = ["C:\\npm\\claude.cmd", "mcp", "add", argument, "--", "uv"]
        script, received = arguments_received_through_batch_file(
            windows_batch_command_line(command)
        )
        assert script == command[0]
        assert received == command[1:]

    def test_all_arguments_together_arrive_literally(self):
        command = ["C:\\npm\\claude.cmd", *LITERAL_ARGUMENTS]
        _, received = arguments_received_through_batch_file(
            windows_batch_command_line(command)
        )
        assert received == LITERAL_ARGUMENTS

    @pytest.mark.parametrize(
        "script",
        [
            "C:\\Users\\R&D (x86)\\npm\\claude.cmd",
            "C:\\Users\\100%\\npm\\gemini.cmd",
        ],
    )
    def test_script_path_arrives_literally(self, script: str):
        found, received = arguments_received_through_batch_file(
            windows_batch_command_line([script, "%x%"])
        )
        assert found == script
        assert received == ["%x%"]

    @pytest.mark.parametrize(
        "script", ['C:\\npm\\"claude.cmd', "C:\\npm\\", "C:\\npm\nclaude.cmd"]
    )
    def test_rejects_invalid_script_path(self, script: str):
        with pytest.raises(ValueError, match="batch file path"):
            windows_batch_command_line([script, "mcp"])


@dataclass
class Installer:
    install: Callable[..., bool]
    command: Callable[..., Coroutine[Any, Any, None]]
    module: ModuleType
    find_command: str
    executable_name: str


INSTALLERS = [
    Installer(
        install=claude_code.install_claude_code,
        command=claude_code.claude_code_command,
        module=claude_code,
        find_command="find_claude_command",
        executable_name="claude",
    ),
    Installer(
        install=gemini_cli.install_gemini_cli,
        command=gemini_cli.gemini_cli_command,
        module=gemini_cli,
        find_command="find_gemini_command",
        executable_name="gemini",
    ),
]


@pytest.fixture(params=INSTALLERS, ids=["claude-code", "gemini-cli"])
def installer(request: pytest.FixtureRequest) -> Installer:
    return request.param


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=""))
    monkeypatch.setattr(shared.subprocess, "run", mock)
    return mock


def use_platform(
    monkeypatch: pytest.MonkeyPatch,
    installer: Installer,
    platform: str,
    executable: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(installer.module, installer.find_command, lambda: executable)
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")


class TestInstallerSubprocessBoundary:
    def test_batch_wrapper_receives_literal_arguments(
        self, installer: Installer, run: Mock, monkeypatch: pytest.MonkeyPatch
    ):
        wrapper = f"C:\\npm\\{installer.executable_name}.cmd"
        use_platform(monkeypatch, installer, "win32", wrapper)
        assert installer.install(
            file=Path("server.py"),
            server_object="mcp",
            name="server",
            with_packages=["pandas>=2.0", 'foo; python_version<"3.12"'],
            env_vars={"API_URL": "https://example.com/?a=1&b=2", "PERCENT": "%PATH%"},
        )

        run.assert_called_once()
        command_line = run.call_args.args[0]
        assert isinstance(command_line, str)
        assert run.call_args.kwargs["executable"] == "C:\\Windows\\System32\\cmd.exe"
        script, received = arguments_received_through_batch_file(command_line)
        assert script == wrapper
        assert "API_URL=https://example.com/?a=1&b=2" in received
        assert "PERCENT=%PATH%" in received
        assert "pandas>=2.0" in received
        assert 'foo; python_version<"3.12"' in received

    @pytest.mark.parametrize("extension", [".cmd", ".CMD", ".bat", ".Bat"])
    def test_batch_wrapper_detection_ignores_case(
        self,
        installer: Installer,
        run: Mock,
        monkeypatch: pytest.MonkeyPatch,
        extension: str,
    ):
        wrapper = f"C:\\npm\\{installer.executable_name}{extension}"
        use_platform(monkeypatch, installer, "win32", wrapper)
        assert installer.install(file=Path("server.py"), server_object=None, name="s")
        assert run.call_args.args[0].startswith(CMD_PREFIX)

    @pytest.mark.parametrize(
        "platform,executable",
        [
            ("win32", "C:\\Program Files\\client\\client.exe"),
            ("darwin", "/usr/local/bin/client.cmd"),
            ("linux", "/usr/local/bin/client"),
        ],
    )
    def test_other_executables_receive_argument_list(
        self,
        installer: Installer,
        run: Mock,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        executable: str,
    ):
        use_platform(monkeypatch, installer, platform, executable)
        assert installer.install(
            file=Path("server.py"),
            server_object=None,
            name="server",
            env_vars={"API_URL": "https://example.com/?a=1&b=2 %PATH%"},
        )

        argv = run.call_args.args[0]
        assert isinstance(argv, list)
        assert argv[0] == executable
        assert "API_URL=https://example.com/?a=1&b=2 %PATH%" in argv
        assert "executable" not in run.call_args.kwargs

    def test_batch_wrapper_rejects_line_break(
        self, installer: Installer, run: Mock, monkeypatch: pytest.MonkeyPatch
    ):
        use_platform(
            monkeypatch, installer, "win32", f"{installer.executable_name}.cmd"
        )
        assert not installer.install(
            file=Path("server.py"),
            server_object=None,
            name="server",
            env_vars={"GREETING": "hello\nworld"},
        )
        run.assert_not_called()

    async def test_config_and_env_file_values_reach_wrapper_literally(
        self,
        installer: Installer,
        run: Mock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        wrapper = f"C:\\npm\\{installer.executable_name}.cmd"
        use_platform(monkeypatch, installer, "win32", wrapper)
        (tmp_path / "server.py").write_text(
            'from fastmcp import FastMCP\n\nmcp = FastMCP("Analytics")\n'
        )
        config = tmp_path / "fastmcp.json"
        config.write_text(
            json.dumps(
                {
                    "source": {"path": "server.py", "entrypoint": "mcp"},
                    "environment": {
                        "dependencies": ["pandas>=2.0", 'tomli; python_version<"3.11"']
                    },
                }
            )
        )
        env_file = tmp_path / ".env"
        env_file.write_text("API_URL='https://example.com/?a=1&b=2 100% !'\n")

        await installer.command(str(config), server_name="server", env_file=env_file)

        _, received = arguments_received_through_batch_file(run.call_args.args[0])
        assert "API_URL=https://example.com/?a=1&b=2 100% !" in received
        assert "pandas>=2.0" in received
        assert 'tomli; python_version<"3.11"' in received


def test_run_cli_command_passes_argument_list(run: Mock):
    run_cli_command(["client", "mcp", "add"])
    run.assert_called_once_with(
        ["client", "mcp", "add"], check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "comspec,expected",
    [
        ("C:\\Tools\\cmd.exe", "C:\\Tools\\cmd.exe"),
        ("cmd.exe", "D:\\Win\\System32\\cmd.exe"),
        (None, "D:\\Win\\System32\\cmd.exe"),
    ],
)
def test_batch_file_runs_with_absolute_cmd_path(
    run: Mock, monkeypatch: pytest.MonkeyPatch, comspec: str | None, expected: str
):
    monkeypatch.setattr(sys, "platform", "win32")
    if comspec is None:
        monkeypatch.delenv("COMSPEC", raising=False)
    else:
        monkeypatch.setenv("COMSPEC", comspec)
    monkeypatch.setenv("SYSTEMROOT", "D:\\Win")
    run_cli_command(["C:\\npm\\claude.cmd", "mcp"])
    assert run.call_args.kwargs["executable"] == expected


ARGV_RECORDER = """\
import json
import os
import sys

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "argv.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(sys.argv[1:], f)
"""

# Same structure as the wrappers npm's cmd-shim generates, with Python standing
# in for node.exe. The last line expands `%*`, so cmd.exe parses the
# arguments a second time.
CMD_SHIM = (
    "@ECHO off\r\n"
    "GOTO start\r\n"
    ":find_dp0\r\n"
    "SET dp0=%~dp0\r\n"
    "EXIT /b\r\n"
    ":start\r\n"
    "SETLOCAL\r\n"
    "CALL :find_dp0\r\n"
    'SET "_prog={python}"\r\n'
    "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "
    '"%_prog%"  "%dp0%\\record_argv.py" %*\r\n'
)


@pytest.mark.skipif(sys.platform != "win32", reason="Runs a real .cmd file")
@pytest.mark.timeout(30)
def test_native_windows_cmd_wrapper_receives_literal_arguments(
    installer: Installer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    wrapper_dir = tmp_path / "npm (x86)"
    wrapper_dir.mkdir()
    (wrapper_dir / "record_argv.py").write_text(ARGV_RECORDER, encoding="utf-8")
    wrapper = wrapper_dir / f"{installer.executable_name}.cmd"
    wrapper.write_bytes(CMD_SHIM.replace("{python}", sys.executable).encode("utf-8"))
    monkeypatch.setattr(installer.module, installer.find_command, lambda: str(wrapper))

    server = tmp_path / "project (1)" / "server.py"
    server.parent.mkdir()
    server.write_text("")

    env_vars = {
        "API_URL": "https://example.com/api?user=me&page=2",
        "SEARCH": "cats|dogs",
        "PATTERN": "^[a-z]+$",
        "GREETING": 'say "hi" & wave',
        "PERCENT": "%PATH% 100%",
        "EXCLAIM": "hello!",
        "CONFIG": 'json={"key": "value"}',
        "DATA_DIR": "C:\\data\\",
    }
    packages = [
        "pandas>=2.0",
        "numpy!=1.0",
        'foo[extra]>=1,<2; python_version<"3.12"',
        "pkg @ https://example.com/pkg.whl?a=1&b=2",
    ]
    project = Path("C:\\Program Files (x86)\\x")
    assert installer.install(
        file=server,
        server_object="mcp",
        name="server",
        with_packages=packages,
        env_vars=env_vars,
        project=project,
    )

    full_command = UVEnvironment(
        dependencies=[*packages, "fastmcp"], project=project
    ).build_command(["fastmcp", "run", f"{server.resolve()}:mcp"])
    env_arguments = [
        argument
        for key, value in env_vars.items()
        for argument in ("-e", f"{key}={value}")
    ]
    if installer.module is claude_code:
        expected = ["mcp", "add", "server", *env_arguments, "--", *full_command]
    else:
        expected = [
            "mcp",
            "add",
            *env_arguments,
            "server",
            full_command[0],
            "--",
            *full_command[1:],
        ]

    received = json.loads((wrapper_dir / "argv.json").read_text(encoding="utf-8"))
    assert received == expected
