"""CommandBackedTool: MCP Tool that runs a subprocess (no shell)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.types import TextContent

from fastmcp.server.providers.command_config.models import (
    CommandToolSpec,
    ParameterSpec,
)
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.tools.tool import Tool, ToolResult


def parameter_spec_to_json_schema(parameters: dict[str, ParameterSpec]) -> dict[str, Any]:
    """Build JSON Schema object for MCP tool parameters."""
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
    """Apply defaults, validate required fields, coerce types."""
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
                return (
                    {},
                    f"Parameter {param_name} must be a number, got {type(raw).__name__}",
                )
        elif param_spec.type == "integer":
            try:
                resolved[param_name] = int(raw)
            except (TypeError, ValueError):
                return (
                    {},
                    f"Parameter {param_name} must be an integer, got {type(raw).__name__}",
                )
        elif param_spec.type == "boolean":
            if isinstance(raw, bool):
                resolved[param_name] = raw
            elif isinstance(raw, str):
                resolved[param_name] = raw.lower() in ("true", "1", "yes")
            else:
                resolved[param_name] = bool(raw)
    return resolved, None


def _replace_placeholders(
    args_template: list[str], resolved: dict[str, Any]
) -> tuple[list[str], str | None]:
    """Substitute ``{param}`` tokens (whole argv element only)."""
    out: list[str] = []
    for part in args_template:
        if len(part) >= 2 and part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            if key not in resolved:
                return [], f"Placeholder {{{key}}} has no value in arguments"
            out.append(str(resolved[key]))
        else:
            out.append(part)
    return out, None


def _failure_result(
    err: str,
    *,
    command: list[str],
    cwd: str | None,
) -> ToolResult:
    return ToolResult(
        content=TextContent(type="text", text=f"Error: {err}"),
        structured_content={
            "ok": False,
            "error": err,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "command": command,
            "working_dir": cwd,
        },
    )


class CommandBackedTool(Tool):
    """Runs ``command`` + resolved ``args_template`` via :func:`asyncio.create_subprocess_exec`.

    Security: ``shell`` is never used; arguments are passed as a list (no shell injection
    from metacharacters in parameter values). Users must still trust configured executables
    and working directories.
    """

    task_config: TaskConfig = TaskConfig(mode="forbidden")

    def __init__(self, tool_spec: CommandToolSpec, parameters_schema: dict[str, Any]):
        # Intentionally omit Tool.timeout: timeout is enforced only around communicate()
        # to avoid stacking with server-level tool timeouts.
        super().__init__(
            name=tool_spec.name,
            description=tool_spec.description,
            parameters=parameters_schema,
        )
        self._tool_spec = tool_spec

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        spec = self._tool_spec
        resolved, err = _resolve_arguments(spec, arguments)
        if err:
            return _failure_result(err, command=[], cwd=spec.working_dir)

        resolved_args, err = _replace_placeholders(spec.args_template, resolved)
        if err:
            return _failure_result(err, command=[], cwd=spec.working_dir)

        cmd_list = [spec.command, *resolved_args]
        cwd: str | None = spec.working_dir
        if cwd is not None:
            cwd_path = Path(cwd)
            if not cwd_path.is_dir():
                return _failure_result(
                    f"working_dir does not exist or is not a directory: {cwd}",
                    command=cmd_list,
                    cwd=cwd,
                )

        env = os.environ.copy()
        env.update(spec.env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            timeout = spec.timeout_seconds
            if timeout is not None and timeout > 0:
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=float(timeout)
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    msg = f"Command timed out after {timeout} seconds"
                    return _failure_result(msg, command=cmd_list, cwd=cwd)
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
                text = (
                    f"Command failed.\nexit_code: {exit_code}\nstderr:\n"
                    f"{stderr_str}\nstdout:\n{stdout_str}"
                )

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
            return _failure_result(err_msg, command=cmd_list, cwd=cwd)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            return _failure_result(err_msg, command=cmd_list, cwd=cwd)
