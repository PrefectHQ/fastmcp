"""Generated CLIs opt into tasks without requiring the optional package."""

import subprocess
import sys
import textwrap
from pathlib import Path

import mcp_types
import pytest

from fastmcp.cli.generate import generate_cli_script


@pytest.fixture
def generated_cli(tmp_path: Path) -> Path:
    server = tmp_path / "server.py"
    server.write_text(
        textwrap.dedent("""\
            from fastmcp import FastMCP
            from fastmcp.utilities.tasks import TaskConfig
            from fastmcp_tasks import TasksExtension

            server = FastMCP("Tasks")
            server.add_extension(TasksExtension())

            @server.tool(task=TaskConfig(mode="required"))
            async def background() -> str:
                return "background completed"

            @server.tool(task=TaskConfig(mode="required"))
            async def failing() -> str:
                raise ValueError("background failed")

            @server.tool
            def foreground() -> str:
                return "foreground completed"

            server.run(show_banner=False)
            """),
        encoding="utf-8",
    )
    transport = {
        "mcpServers": {"tasks": {"command": sys.executable, "args": [str(server)]}}
    }
    cli = tmp_path / "cli.py"
    cli.write_text(
        generate_cli_script(
            "Tasks",
            str(server),
            repr(transport),
            set(),
            [
                mcp_types.Tool(
                    name=name, input_schema={"type": "object", "properties": {}}
                )
                for name in ("background", "failing", "foreground")
            ],
        ),
        encoding="utf-8",
    )
    return cli


@pytest.mark.subprocess_heavy
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    ("tool", "exit_code", "output"),
    [
        ("background", 0, "background completed"),
        ("failing", 1, "background failed"),
        ("foreground", 0, "foreground completed"),
    ],
)
def test_generated_cli_waits_for_task_result(
    generated_cli: Path, tool: str, exit_code: int, output: str
) -> None:
    result = subprocess.run(
        [sys.executable, str(generated_cli), "call-tool", tool],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert output in result.stdout, result.stdout + result.stderr


@pytest.mark.subprocess_heavy
@pytest.mark.timeout(30)
def test_generated_cli_without_tasks_package(generated_cli: Path) -> None:
    runner = textwrap.dedent("""\
        import importlib.abc
        import runpy
        import sys

        class MissingTasks(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "fastmcp_tasks":
                    raise ModuleNotFoundError("No tasks package", name=fullname)

        sys.meta_path.insert(0, MissingTasks())
        sys.argv = sys.argv[1:]
        runpy.run_path(sys.argv[0], run_name="__main__")
        """)
    result = subprocess.run(
        [sys.executable, "-c", runner, str(generated_cli), "call-tool", "foreground"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "foreground completed" in result.stdout
