"""Pydantic models for command-backed MCP tools loaded from YAML/JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self


class ParameterSpec(BaseModel):
    """Single tool parameter schema (maps to JSON Schema for MCP)."""

    type: Literal["string", "number", "integer", "boolean"] = Field(
        description="JSON Schema type for the parameter"
    )
    description: str | None = Field(default=None, description="Parameter description")
    required: bool = Field(default=False, description="Whether the parameter is required")
    default: Any = Field(default=None, description="Default when not provided by the client")


class CommandToolSpec(BaseModel):
    """One command-backed tool definition."""

    name: str = Field(description="Tool name exposed to MCP clients")
    description: str = Field(description="Tool description")
    command: str = Field(
        description="Executable name or path (first argv element; never passed to a shell)"
    )
    args_template: list[str] = Field(
        default_factory=list,
        description=(
            "Argument list. A token that is exactly '{param_name}' is replaced with the "
            "string form of that parameter; all other tokens are passed through unchanged."
        ),
    )
    working_dir: str | None = Field(
        default=None,
        description="Process working directory, or None for server cwd",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables (merged over os.environ)",
    )
    timeout_seconds: int | None = Field(
        default=None,
        description="Subprocess timeout in seconds; None means no timeout",
    )
    parameters: dict[str, ParameterSpec] = Field(
        default_factory=dict,
        description="Parameter definitions for validation and JSON Schema",
    )


class CommandToolsSpec(BaseModel):
    """Root document: optional server display name and list of tools."""

    server_name: str | None = Field(default=None, description="Suggested MCP server name")
    tools: list[CommandToolSpec] = Field(
        default_factory=list,
        description="Command-backed tools to expose",
    )

    @model_validator(mode="after")
    def _tool_names_unique(self) -> Self:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for t in self.tools:
            if t.name in seen:
                duplicates.add(t.name)
            seen.add(t.name)
        if duplicates:
            raise ValueError(
                f"Duplicate tool name(s) in command tools config: {sorted(duplicates)}"
            )
        return self


def parse_command_tools_document(raw: str, *, suffix: str) -> dict[str, Any]:
    """Parse YAML or JSON text into a dict for model_validate."""
    suf = suffix.lower()
    if suf in (".yaml", ".yml"):
        data = yaml.safe_load(raw)
    elif suf == ".json":
        data = json.loads(raw)
    else:
        raise ValueError(
            f"Unsupported config format {suffix!r}. Use .yaml, .yml, or .json"
        )
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            "Command tools config root must be a JSON object or YAML mapping, not a list or scalar"
        )
    return data


def apply_working_dir_base(spec: CommandToolsSpec, config_path: Path) -> CommandToolsSpec:
    """Resolve relative working_dir values against the config file's parent directory."""
    base = config_path.resolve().parent
    new_tools: list[CommandToolSpec] = []
    for t in spec.tools:
        wd = t.working_dir
        if wd is None:
            new_tools.append(t)
            continue
        p = Path(wd)
        if p.is_absolute():
            new_tools.append(t)
        else:
            new_tools.append(
                t.model_copy(update={"working_dir": str((base / p).resolve())})
            )
    return spec.model_copy(update={"tools": new_tools})
