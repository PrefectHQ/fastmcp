"""Expose tools that their author explicitly marked read-only."""

from collections.abc import Sequence

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec


def _read_only(tool: Tool) -> bool:
    return tool.annotations is not None and tool.annotations.read_only_hint is True


class ReadOnly(Transform):
    """Restrict the tool catalog and lookups to tools with `readOnlyHint=True`."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if _read_only(tool)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if tool is not None and _read_only(tool) else None
