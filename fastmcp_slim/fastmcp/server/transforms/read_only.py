"""Expose tools that their author explicitly marked read-only."""

from collections.abc import Sequence

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import VersionSpec


class ReadOnly(Transform):
    """Restrict the tool catalog and lookups to tools with `readOnlyHint=True`."""

    @staticmethod
    def allows_tool(tool: Tool) -> bool:
        """Return whether the tool explicitly declares read-only behavior."""
        return tool.annotations is not None and tool.annotations.read_only_hint is True

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if self.allows_tool(tool)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        while tool is not None and not self.allows_tool(tool):
            if tool.version is None or (version is not None and version.eq is not None):
                return None
            # Ask the same lookup chain for the next lower version. Unversioned
            # tools match range specs and are considered last.
            older = VersionSpec(lt=tool.version).intersect(version)
            tool = await call_next(name, version=older)
            if tool is not None and not older.matches(tool.version):
                # A provider may ignore lookup constraints.
                return None
            version = older
        return tool
