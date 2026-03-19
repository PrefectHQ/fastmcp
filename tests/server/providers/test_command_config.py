"""Tests for CommandConfigProvider and command_config models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.providers.command_config import (
    CommandBackedTool,
    CommandConfigProvider,
    CommandToolSpec,
    ParameterSpec,
    load_command_tools_spec,
    parameter_spec_to_json_schema,
)


def _write_echo_and_fail_scripts(project: Path) -> None:
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "echo_tool.py").write_text(
        """import argparse
import sys
p = argparse.ArgumentParser()
p.add_argument("--msg", required=True)
args = p.parse_args()
print(args.msg)
sys.exit(0)
""",
        encoding="utf-8",
    )
    (scripts / "fail_stub.py").write_text(
        """import sys
print("err", file=sys.stderr)
sys.exit(1)
""",
        encoding="utf-8",
    )


def test_load_command_tools_spec_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
server_name: "TestServer"
tools:
  - name: "echo_tool"
    description: "Echo"
    command: "python"
    args_template: ["scripts/echo.py", "--msg", "{msg}"]
    parameters:
      msg:
        type: "string"
        description: "Message"
        required: true
""",
        encoding="utf-8",
    )
    spec = load_command_tools_spec(config_file, resolve_relative_working_dirs=False)
    assert spec.server_name == "TestServer"
    assert len(spec.tools) == 1
    assert spec.tools[0].name == "echo_tool"


def test_load_command_tools_spec_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        '{"server_name": "J", "tools": [{"name": "t", "description": "d", "command": "python", "args_template": [], "parameters": {}}]}',
        encoding="utf-8",
    )
    spec = load_command_tools_spec(config_file, resolve_relative_working_dirs=False)
    assert spec.server_name == "J"


def test_load_command_tools_spec_bad_extension(tmp_path: Path) -> None:
    config_file = tmp_path / "config.txt"
    config_file.write_text("tools: []", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported config format"):
        load_command_tools_spec(config_file)


def test_load_command_tools_spec_duplicate_names(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
tools:
  - name: "dup"
    description: "a"
    command: "python"
  - name: "dup"
    description: "b"
    command: "python"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Duplicate tool name"):
        load_command_tools_spec(config_file, resolve_relative_working_dirs=False)


def test_resolve_relative_working_dir(tmp_path: Path) -> None:
    sub = tmp_path / "proj"
    sub.mkdir()
    config_file = sub / "config.yaml"
    config_file.write_text(
        """
tools:
  - name: "t"
    description: "d"
    command: "python"
    working_dir: "."
""",
        encoding="utf-8",
    )
    spec = load_command_tools_spec(config_file, resolve_relative_working_dirs=True)
    assert spec.tools[0].working_dir == str(sub.resolve())


@pytest.fixture
def echo_tool_spec(tmp_path: Path) -> CommandToolSpec:
    _write_echo_and_fail_scripts(tmp_path)
    return CommandToolSpec(
        name="echo_tool",
        description="Echo",
        command=sys.executable,
        args_template=["scripts/echo_tool.py", "--msg", "{msg}"],
        working_dir=str(tmp_path),
        parameters={
            "msg": ParameterSpec(type="string", description="Msg", required=True),
        },
    )


@pytest.mark.asyncio
async def test_command_backed_tool_missing_required(echo_tool_spec: CommandToolSpec) -> None:
    schema = parameter_spec_to_json_schema(echo_tool_spec.parameters)
    tool = CommandBackedTool(echo_tool_spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "Missing required" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_command_backed_tool_placeholder_missing(echo_tool_spec: CommandToolSpec) -> None:
    schema = parameter_spec_to_json_schema(echo_tool_spec.parameters)
    tool = CommandBackedTool(echo_tool_spec, schema)
    result = await tool.run({"other": "x"})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False


@pytest.mark.asyncio
async def test_command_backed_tool_echo_success(echo_tool_spec: CommandToolSpec) -> None:
    schema = parameter_spec_to_json_schema(echo_tool_spec.parameters)
    tool = CommandBackedTool(echo_tool_spec, schema)
    result = await tool.run({"msg": "hello"})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is True
    assert result.structured_content["exit_code"] == 0
    assert "hello" in result.structured_content["stdout"]


@pytest.mark.asyncio
async def test_command_backed_tool_fail_exit(tmp_path: Path) -> None:
    _write_echo_and_fail_scripts(tmp_path)
    spec = CommandToolSpec(
        name="fail_stub",
        description="Fails",
        command=sys.executable,
        args_template=["scripts/fail_stub.py"],
        working_dir=str(tmp_path),
        parameters={},
    )
    schema = parameter_spec_to_json_schema(spec.parameters)
    tool = CommandBackedTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert result.structured_content["exit_code"] == 1


@pytest.mark.asyncio
async def test_command_backed_tool_timeout() -> None:
    spec = CommandToolSpec(
        name="sleep_tool",
        description="Sleep",
        command=sys.executable,
        args_template=["-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
        parameters={},
    )
    schema = parameter_spec_to_json_schema(spec.parameters)
    tool = CommandBackedTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    err = result.structured_content.get("error", "").lower()
    assert "timeout" in err or "timed out" in err


@pytest.mark.asyncio
async def test_command_backed_tool_working_dir_missing() -> None:
    spec = CommandToolSpec(
        name="bad",
        description="Bad",
        command=sys.executable,
        args_template=["-c", "print(1)"],
        working_dir="/nonexistent/dir/12345",
        parameters={},
    )
    schema = parameter_spec_to_json_schema(spec.parameters)
    tool = CommandBackedTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "working_dir" in result.structured_content.get("error", "")


@pytest.mark.asyncio
async def test_command_config_provider_client(tmp_path: Path) -> None:
    _write_echo_and_fail_scripts(tmp_path)
    exe = json.dumps(sys.executable)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
server_name: "CmdCfgTest"
tools:
  - name: "echo_tool"
    description: "Echo"
    command: {exe}
    args_template: ["scripts/echo_tool.py", "--msg", "{{msg}}"]
    working_dir: "."
    parameters:
      msg:
        type: "string"
        required: true
  - name: "fail_stub"
    description: "Fail"
    command: {exe}
    args_template: ["scripts/fail_stub.py"]
    working_dir: "."
    parameters: {{}}
""",
        encoding="utf-8",
    )
    spec = load_command_tools_spec(config_file)
    provider = CommandConfigProvider(spec)
    mcp = FastMCP(spec.server_name or "CmdCfgTest", providers=[provider])

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "echo_tool" in names
        assert "fail_stub" in names

        result = await client.call_tool("echo_tool", {"msg": "integration_test"})
        assert result.structured_content is not None
        assert result.structured_content.get("ok") is True
        assert "integration_test" in result.structured_content.get("stdout", "")

        result_fail = await client.call_tool("fail_stub", {})
        assert result_fail.structured_content is not None
        assert result_fail.structured_content.get("ok") is False
        assert result_fail.structured_content.get("exit_code") == 1
