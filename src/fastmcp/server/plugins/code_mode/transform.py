"""Low-level transform that powers the CodeMode plugin.

`CodeModeTransform` replaces the tool catalog with two classes of
meta-tool: configurable **discovery tools** (search, get_schema, etc.)
that let the LLM explore what's available, and a single **execute tool**
that runs LLM-generated Python in a sandbox with `call_tool(...)`
available in scope.

Most users should configure CodeMode through the `CodeMode` plugin
(`fastmcp.server.plugins.code_mode`). The transform is exposed for
advanced composition — users who want to stack it with other transforms
directly or embed it in a custom plugin.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from mcp.types import TextContent
from pydantic import Field

from fastmcp.exceptions import NotFoundError
from fastmcp.server.context import Context
from fastmcp.server.plugins.code_mode.discovery import (
    DiscoveryToolFactory,
    GetSchemas,
    Search,
)
from fastmcp.server.plugins.code_mode.sandbox import (
    MontySandboxProvider,
    SandboxProvider,
)
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec


def _unwrap_tool_result(result: ToolResult) -> dict[str, Any] | str:
    """Convert a ToolResult for use in the sandbox.

    - Output schema present → structured_content dict (matches the schema)
    - Otherwise → concatenated text content as a string
    """
    if result.structured_content is not None:
        return result.structured_content

    parts: list[str] = []
    for content in result.content:
        if isinstance(content, TextContent):
            parts.append(content.text)
        else:
            parts.append(str(content))
    return "\n".join(parts)


def _default_discovery_tools() -> list[DiscoveryToolFactory]:
    return [Search(), GetSchemas()]


class CodeModeTransform(CatalogTransform):
    """Transform that collapses all tools into discovery + execute meta-tools.

    Discovery tools are composable via the `discovery_tools` parameter.
    Each is a callable that receives catalog access and returns a `Tool`.
    By default, `Search` and `GetSchemas` are included for progressive
    disclosure: search finds candidates, get_schema retrieves parameter
    details, and execute runs code.

    The `execute` tool is always present and provides a sandboxed Python
    environment with `call_tool(name, params)` in scope.
    """

    def __init__(
        self,
        *,
        sandbox_provider: SandboxProvider | None = None,
        discovery_tools: list[DiscoveryToolFactory] | None = None,
        execute_tool_name: str = "execute",
        execute_description: str | None = None,
    ) -> None:
        super().__init__()
        self.execute_tool_name = execute_tool_name
        self.execute_description = execute_description
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()

        self._discovery_factories = (
            discovery_tools
            if discovery_tools is not None
            else _default_discovery_tools()
        )
        self._built_discovery_tools: list[Tool] | None = None
        self._cached_execute_tool: Tool | None = None

    def _build_discovery_tools(self) -> list[Tool]:
        if self._built_discovery_tools is None:
            tools = [
                factory(self.get_tool_catalog) for factory in self._discovery_factories
            ]
            names = {t.name for t in tools}
            if self.execute_tool_name in names:
                raise ValueError(
                    f"Discovery tool name '{self.execute_tool_name}' "
                    f"collides with execute_tool_name."
                )
            if len(names) != len(tools):
                raise ValueError("Discovery tools must have unique names.")
            self._built_discovery_tools = tools
        return self._built_discovery_tools

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [*self._build_discovery_tools(), self._get_execute_tool()]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        for tool in self._build_discovery_tools():
            if tool.name == name:
                return tool
        if name == self.execute_tool_name:
            return self._get_execute_tool()
        return await call_next(name, version=version)

    def _build_execute_description(self) -> str:
        if self.execute_description is not None:
            return self.execute_description

        return (
            "Chain `await call_tool(...)` calls in one Python block; prefer returning the final answer from a single block.\n"
            "Use `return` to produce output.\n"
            "Only `call_tool(tool_name: str, params: dict) -> Any` is available in scope."
        )

    @staticmethod
    def _find_tool(name: str, tools: Sequence[Tool]) -> Tool | None:
        """Find a tool by name from a pre-fetched list."""
        for tool in tools:
            if tool.name == name:
                return tool
        return None

    def _get_execute_tool(self) -> Tool:
        if self._cached_execute_tool is None:
            self._cached_execute_tool = self._make_execute_tool()
        return self._cached_execute_tool

    def _make_execute_tool(self) -> Tool:
        transform = self

        async def execute(
            code: Annotated[
                str,
                Field(
                    description=(
                        "Python async code to execute tool calls via call_tool(name, arguments)"
                    )
                ),
            ],
            ctx: Context = None,  # type: ignore[assignment]  # ty:ignore[invalid-parameter-default]
        ) -> Any:
            """Execute tool calls using Python code."""

            async def call_tool(tool_name: str, params: dict[str, Any]) -> Any:
                backend_tools = await transform.get_tool_catalog(ctx)
                tool = transform._find_tool(tool_name, backend_tools)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {tool_name}")

                result = await ctx.fastmcp.call_tool(tool.name, params)
                return _unwrap_tool_result(result)

            return await transform.sandbox_provider.run(
                code,
                external_functions={"call_tool": call_tool},
            )

        return Tool.from_function(
            fn=execute,
            name=self.execute_tool_name,
            description=self._build_execute_description(),
        )
