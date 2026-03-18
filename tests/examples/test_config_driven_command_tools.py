"""Unit and integration tests for config_driven_command_tools example."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add example directory so we can import config and command_tool_provider
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "config_driven_command_tools"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

from command_tool_provider import CommandTool, CommandToolProvider  # noqa: E402
from config import (  # noqa: E402
    CommandToolSpec,
    ParameterSpec,
    load_config,
)

# ---------------------------------------------------------------------------
# Unit: config loading
# ---------------------------------------------------------------------------


def test_load_config_yaml(tmp_path: Path) -> None:
    """load_config parses YAML and returns CommandToolsSpec."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
server_name: "TestServer"
tools:
  - name: "echo_tool"
    description: "Echo a message"
    command: "python"
    args_template: ["scripts/echo.py", "--msg", "{msg}"]
    parameters:
      msg:
        type: "string"
        description: "Message"
        required: true
"""
    )
    spec = load_config(config_file)
    assert spec.server_name == "TestServer"
    assert len(spec.tools) == 1
    assert spec.tools[0].name == "echo_tool"
    assert spec.tools[0].command == "python"
    assert spec.tools[0].args_template == ["scripts/echo.py", "--msg", "{msg}"]


def test_load_config_json(tmp_path: Path) -> None:
    """load_config parses JSON and returns CommandToolsSpec."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        '{"server_name": "J", "tools": [{"name": "t", "description": "d", "command": "python", "args_template": [], "parameters": {}}]}'
    )
    spec = load_config(config_file)
    assert spec.server_name == "J"
    assert len(spec.tools) == 1
    assert spec.tools[0].name == "t"


def test_load_config_invalid_extension(tmp_path: Path) -> None:
    """load_config raises for unsupported extension."""
    config_file = tmp_path / "config.txt"
    config_file.write_text("tools: []")
    with pytest.raises(ValueError, match="Unsupported config format"):
        load_config(config_file)


def test_load_config_validation_error(tmp_path: Path) -> None:
    """load_config raises on invalid structure."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("tools: [{name: 123}]")  # name should be string
    with pytest.raises(Exception):  # Pydantic ValidationError
        load_config(config_file)


# ---------------------------------------------------------------------------
# Unit: CommandTool parameter resolution and execution
# ---------------------------------------------------------------------------


@pytest.fixture
def echo_spec() -> CommandToolSpec:
    """Minimal echo tool spec with one required param."""
    return CommandToolSpec(
        name="echo_tool",
        description="Echo",
        command="python",
        args_template=["scripts/echo_tool.py", "--msg", "{msg}"],
        working_dir=str(EXAMPLE_DIR),
        parameters={
            "msg": ParameterSpec(type="string", description="Msg", required=True),
        },
    )


@pytest.fixture
def echo_schema() -> dict:
    """JSON schema for echo_tool parameters."""
    return {
        "type": "object",
        "properties": {"msg": {"type": "string", "description": "Msg"}},
        "required": ["msg"],
    }


@pytest.mark.asyncio
async def test_command_tool_missing_required(echo_spec: CommandToolSpec, echo_schema: dict) -> None:
    """CommandTool.run returns error when required param is missing."""
    tool = CommandTool(echo_spec, echo_schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "Missing required" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_command_tool_placeholder_missing(echo_spec: CommandToolSpec, echo_schema: dict) -> None:
    """CommandTool.run returns error when placeholder has no value."""
    tool = CommandTool(echo_spec, echo_schema)
    result = await tool.run({"other": "x"})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "Placeholder" in result.structured_content["error"] or "Missing" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_command_tool_echo_success(echo_spec: CommandToolSpec, echo_schema: dict) -> None:
    """CommandTool.run executes echo script and returns stdout."""
    tool = CommandTool(echo_spec, echo_schema)
    result = await tool.run({"msg": "hello"})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is True
    assert result.structured_content["exit_code"] == 0
    assert "hello" in result.structured_content["stdout"]


@pytest.mark.asyncio
async def test_command_tool_fail_stub() -> None:
    """CommandTool.run returns ok=False and exit_code 1 for failing script."""
    spec = CommandToolSpec(
        name="fail_stub",
        description="Fails",
        command="python",
        args_template=["scripts/fail_stub.py"],
        working_dir=str(EXAMPLE_DIR),
        parameters={},
    )
    schema = {"type": "object", "properties": {}, "required": []}
    tool = CommandTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert result.structured_content["exit_code"] == 1
    assert "stderr" in result.structured_content


@pytest.mark.asyncio
async def test_command_tool_timeout() -> None:
    """CommandTool.run returns timeout error when command exceeds timeout_seconds."""
    spec = CommandToolSpec(
        name="sleep_tool",
        description="Sleep",
        command="python",
        args_template=["-c", "import time; time.sleep(10)"],
        working_dir=str(EXAMPLE_DIR),
        timeout_seconds=1,
        parameters={},
    )
    schema = {"type": "object", "properties": {}, "required": []}
    tool = CommandTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    error_msg = result.structured_content.get("error", "").lower()
    assert "timeout" in error_msg or "timed out" in error_msg


@pytest.mark.asyncio
async def test_command_tool_working_dir_not_exists() -> None:
    """CommandTool.run returns error when working_dir does not exist."""
    spec = CommandToolSpec(
        name="bad",
        description="Bad",
        command="python",
        args_template=["-c", "print(1)"],
        working_dir="/nonexistent/dir/12345",
        parameters={},
    )
    schema = {"type": "object", "properties": {}, "required": []}
    tool = CommandTool(spec, schema)
    result = await tool.run({})
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "working_dir" in result.structured_content.get("error", "")


# ---------------------------------------------------------------------------
# Integration: list_tools and call_tool via Client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_list_tools_and_call_echo() -> None:
    """Provider exposes tools; Client can list and call echo_tool."""
    from fastmcp import Client, FastMCP

    config_path = EXAMPLE_DIR / "config.yaml"
    spec = load_config(config_path)
    # Resolve working_dir relative to config (same as server.py)
    config_dir = config_path.resolve().parent
    for tool in spec.tools:
        if tool.working_dir is not None:
            wd = Path(tool.working_dir)
            if not wd.is_absolute():
                tool.working_dir = str((config_dir / wd).resolve())
    provider = CommandToolProvider(spec)
    mcp = FastMCP(spec.server_name or "Command Tools Example", providers=[provider])

    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
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
