"""Config-driven command-backed tools (YAML/JSON) as a first-class Provider."""

from fastmcp.server.providers.command_config.loader import load_command_tools_spec
from fastmcp.server.providers.command_config.models import (
    CommandToolSpec,
    CommandToolsSpec,
    ParameterSpec,
)
from fastmcp.server.providers.command_config.provider import CommandConfigProvider
from fastmcp.server.providers.command_config.tool import (
    CommandBackedTool,
    parameter_spec_to_json_schema,
)

__all__ = [
    "CommandBackedTool",
    "CommandConfigProvider",
    "CommandToolSpec",
    "CommandToolsSpec",
    "ParameterSpec",
    "load_command_tools_spec",
    "parameter_spec_to_json_schema",
]
