"""CommandConfigProvider — exposes command-backed tools from a CommandToolsSpec."""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.command_config.models import CommandToolsSpec
from fastmcp.server.providers.command_config.tool import (
    CommandBackedTool,
    parameter_spec_to_json_schema,
)
from fastmcp.tools.tool import Tool


class CommandConfigProvider(Provider):
    """Provider that registers one MCP tool per entry in :class:`CommandToolsSpec`."""

    def __init__(self, spec: CommandToolsSpec) -> None:
        super().__init__()
        self._spec = spec

    async def _list_tools(self) -> Sequence[Tool]:
        return [
            CommandBackedTool(ts, parameter_spec_to_json_schema(ts.parameters))
            for ts in self._spec.tools
        ]
