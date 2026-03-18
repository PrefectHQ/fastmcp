"""Configuration models and loader for config-driven command tools.

Defines ParameterSpec, CommandToolSpec, CommandToolsSpec and load_config().
Config validation errors are raised at load time so the server fails fast.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class ParameterSpec(BaseModel):
    """Schema for a single tool parameter from config."""

    type: Literal["string", "number", "integer", "boolean"] = Field(
        description="JSON schema type for the parameter"
    )
    description: str | None = Field(default=None, description="Parameter description")
    required: bool = Field(default=False, description="Whether the parameter is required")
    default: Any = Field(default=None, description="Default value when not provided")


class CommandToolSpec(BaseModel):
    """Schema for one command tool from config."""

    name: str = Field(description="Tool name exposed to MCP")
    description: str = Field(description="Tool description")
    command: str = Field(description="Executable (e.g. python, bash)")
    args_template: list[str] = Field(
        default_factory=list,
        description="Argument list with {param_name} placeholders",
    )
    working_dir: str | None = Field(default=None, description="Working directory for execution")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    timeout_seconds: int | None = Field(default=None, description="Execution timeout in seconds")
    parameters: dict[str, ParameterSpec] = Field(
        default_factory=dict,
        description="Parameter definitions for JSON schema and validation",
    )


class CommandToolsSpec(BaseModel):
    """Top-level config: server name and list of command tools."""

    server_name: str | None = Field(default=None, description="MCP server display name")
    tools: list[CommandToolSpec] = Field(
        default_factory=list,
        description="List of command tools to expose",
    )


def load_config(path: Path) -> CommandToolsSpec:
    """Load and validate config from a YAML or JSON file.

    Raises on parse/validation errors so the server fails at startup.
    """
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(raw)
    elif suffix == ".json":
        data = json.loads(raw)
    else:
        raise ValueError(f"Unsupported config format: {path.suffix}. Use .yaml or .json")
    if data is None:
        data = {}
    return CommandToolsSpec.model_validate(data)
