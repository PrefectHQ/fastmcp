"""CommandTool and CommandToolProvider for config-driven command tools.

CommandTool subclasses Tool and runs a local command via subprocess (no shell).
CommandToolProvider implements Provider and exposes tools from CommandToolsSpec.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config import CommandToolSpec, CommandToolsSpec, ParameterSpec
from mcp.types import TextContent

from fastmcp.server.providers.base import Provider
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult


def _parameter_spec_to_json_schema(parameters: dict[str, ParameterSpec]) -> dict[str, Any]:
    """Build JSON Schema for tool parameters from ParameterSpec map."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, spec in parameters.items():
        properties[param_name] = {
            "type": spec.type,
            "description": spec.description or "",
        }
        if spec.required:
            required.append(param_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _resolve_arguments(
    spec: CommandToolSpec, arguments: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Fill defaults, validate required, coerce types. Returns (resolved_args, error_msg)."""
    resolved: dict[str, Any] = dict(arguments)
    for param_name, param_spec in spec.parameters.items():
        if param_name not in resolved:
            if param_spec.default is not None:
                resolved[param_name] = param_spec.default
            elif param_spec.required:
                return {}, f"Missing required parameter: {param_name}"
            else:
                continue
        raw = resolved[param_name]
        if param_spec.type == "string":
            resolved[param_name] = str(raw) if raw is not None else ""
        elif param_spec.type == "number":
            try:
                resolved[param_name] = float(raw)
            except (TypeError, ValueError):
                return {}, f"Parameter {param_name} must be a number, got {type(raw).__name__}"
        elif param_spec.type == "integer":
            try:
                resolved[param_name] = int(raw)
            except (TypeError, ValueError):
                return {}, f"Parameter {param_name} must be an integer, got {type(raw).__name__}"
        elif param_spec.type == "boolean":
            if isinstance(raw, bool):
                resolved[param_name] = raw
            elif isinstance(raw, str):
                resolved[param_name] = raw.lower() in ("true", "1", "yes")
            else:
                resolved[param_name] = bool(raw)
    return resolved, None


def _replace_placeholders(args_template: list[str], resolved: dict[str, Any]) -> tuple[list[str], str | None]:
    """Replace {param} in args_template. Returns (resolved_args, error_msg)."""
    out: list[str] = []
    for part in args_template:
        if not part.startswith("{") or not part.endswith("}"):
            out.append(part)
            continue
        key = part[1:-1]
        if key not in resolved:
            return [], f"Placeholder {{{key}}} has no value in arguments"
        out.append(str(resolved[key]))
    return out, None


class CommandTool(Tool):
    """Tool that runs a local command from CommandToolSpec (no shell)."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(self, spec: CommandToolSpec, parameters_schema: dict[str, Any]):
        super().__init__(
            name=spec.name,
            description=spec.description,
            parameters=parameters_schema,
        )
        self._spec = spec

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the command with resolved arguments; return structured result."""
        resolved, err = _resolve_arguments(self._spec, arguments)
        if err:
            return ToolResult(
                content=TextContent(type="text", text=f"Error: {err}"),
                structured_content={
                    "ok": False,
                    "error": err,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "command": [],
                    "working_dir": self._spec.working_dir,
                },
            )

        resolved_args, err = _replace_placeholders(self._spec.args_template, resolved)
        if err:
            return ToolResult(
                content=TextContent(type="text", text=f"Error: {err}"),
                structured_content={
                    "ok": False,
                    "error": err,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "command": [],
                    "working_dir": self._spec.working_dir,
                },
            )

        cmd_list = [self._spec.command, *resolved_args]
        cwd: str | None = self._spec.working_dir
        if cwd is not None:
            cwd_path = Path(cwd)
            if not cwd_path.is_dir():
                return ToolResult(
                    content=TextContent(
                        type="text",
                        text=f"Error: working_dir does not exist or is not a directory: {cwd}",
                    ),
                    structured_content={
                        "ok": False,
                        "error": f"working_dir does not exist: {cwd}",
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": "",
                        "command": cmd_list,
                        "working_dir": cwd,
                    },
                )

        env = os.environ.copy()
        env.update(self._spec.env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            timeout = self._spec.timeout_seconds
            if timeout is not None and timeout > 0:
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=float(timeout)
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    msg = f"Command timed out after {timeout} seconds"
                    return ToolResult(
                        content=TextContent(type="text", text=f"Error: {msg}"),
                        structured_content={
                            "ok": False,
                            "error": msg,
                            "exit_code": -1,
                            "stdout": "",
                            "stderr": "",
                            "command": cmd_list,
                            "working_dir": cwd,
                        },
                    )
            else:
                stdout_bytes, stderr_bytes = await proc.communicate()

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode if proc.returncode is not None else -1
            ok = exit_code == 0

            if ok:
                text = "Command executed successfully.\nexit_code: 0\nstdout:\n" + stdout_str
                if stderr_str:
                    text += "\nstderr:\n" + stderr_str
            else:
                text = f"Command failed.\nexit_code: {exit_code}\nstderr:\n{stderr_str}\nstdout:\n{stdout_str}"

            return ToolResult(
                content=TextContent(type="text", text=text),
                structured_content={
                    "ok": ok,
                    "exit_code": exit_code,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "command": cmd_list,
                    "working_dir": cwd,
                },
            )
        except FileNotFoundError as e:
            err_msg = f"Command or executable not found: {e}"
            return ToolResult(
                content=TextContent(type="text", text=f"Error: {err_msg}"),
                structured_content={
                    "ok": False,
                    "error": err_msg,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "command": cmd_list,
                    "working_dir": cwd,
                },
            )
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            return ToolResult(
                content=TextContent(type="text", text=f"Error: {err_msg}"),
                structured_content={
                    "ok": False,
                    "error": err_msg,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "command": cmd_list,
                    "working_dir": cwd,
                },
            )


class CommandToolProvider(Provider):
    """Provider that exposes command tools from a CommandToolsSpec."""

    def __init__(self, spec: CommandToolsSpec) -> None:
        super().__init__()
        self._spec = spec

    async def _list_tools(self) -> Sequence[Tool]:
        tools: list[Tool] = []
        for tool_spec in self._spec.tools:
            schema = _parameter_spec_to_json_schema(tool_spec.parameters)
            tools.append(CommandTool(tool_spec, schema))
        return tools
