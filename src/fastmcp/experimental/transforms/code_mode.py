import asyncio
import importlib
import logging
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Protocol

from mcp.types import TextContent
from pydantic import Field

from fastmcp.exceptions import NotFoundError
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.server.transforms.search.base import (
    BaseSearchTransform,
    _serialize_tools_for_output,
)
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

logger = logging.getLogger(__name__)


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    if asyncio.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


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


class SandboxProvider(Protocol):
    """Interface for executing LLM-generated Python code in a sandbox.

    WARNING: The ``code`` parameter passed to ``run`` contains untrusted,
    LLM-generated Python.  Implementations MUST execute it in an isolated
    sandbox — never with plain ``exec()``.  Use ``MontySandboxProvider``
    (backed by ``pydantic-monty``) for production workloads.
    """

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any: ...


class MontySandboxProvider:
    """Sandbox provider backed by `pydantic-monty`.

    Args:
        limits: Resource limits for sandbox execution. Supported keys:
            ``max_duration_secs`` (float), ``max_allocations`` (int),
            ``max_memory`` (int), ``max_recursion_depth`` (int),
            ``gc_interval`` (int).  All are optional; omit a key to
            leave that limit uncapped.
    """

    def __init__(
        self,
        *,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self.limits = limits

    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Callable[..., Any]] | None = None,
    ) -> Any:
        try:
            pydantic_monty = importlib.import_module("pydantic_monty")
        except ModuleNotFoundError as exc:
            raise ImportError(
                "CodeMode requires pydantic-monty for the Monty sandbox provider. "
                "Install it with `fastmcp[code-mode]` or pass a custom SandboxProvider."
            ) from exc

        inputs = inputs or {}
        async_functions = {
            key: _ensure_async(value)
            for key, value in (external_functions or {}).items()
        }

        monty = pydantic_monty.Monty(
            code,
            inputs=list(inputs.keys()),
            external_functions=list(async_functions.keys()),
        )
        run_kwargs: dict[str, Any] = {"external_functions": async_functions}
        if inputs:
            run_kwargs["inputs"] = inputs
        if self.limits is not None:
            run_kwargs["limits"] = self.limits
        return await pydantic_monty.run_monty_async(monty, **run_kwargs)


class CodeMode(CatalogTransform):
    """Transform that collapses all tools into `search` + `execute` meta-tools."""

    def __init__(
        self,
        *,
        default_arguments: dict[str, Any] | None = None,
        sandbox_provider: SandboxProvider | None = None,
        search_transform: BaseSearchTransform | None = None,
        search_tool_name: str = "search",
        execute_tool_name: str = "execute",
        execute_description: str | None = None,
    ) -> None:
        if search_tool_name == execute_tool_name:
            raise ValueError(
                "search_tool_name and execute_tool_name must be different."
            )

        super().__init__()
        self._default_arguments = default_arguments or {}
        self.search_tool_name = search_tool_name
        self.execute_tool_name = execute_tool_name
        self.execute_description = execute_description
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()
        self._cached_search_tool: Tool | None = None
        self._cached_execute_tool: Tool | None = None

        if search_transform is None:
            from fastmcp.server.transforms.search.bm25 import BM25SearchTransform

            search_transform = BM25SearchTransform()
        self._search_transform = search_transform

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._get_search_tool(), self._get_execute_tool()]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        if name == self.search_tool_name:
            return self._get_search_tool()
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

    def _get_search_tool(self) -> Tool:
        if self._cached_search_tool is None:
            self._cached_search_tool = self._make_search_tool()
        return self._cached_search_tool

    def _get_execute_tool(self) -> Tool:
        if self._cached_execute_tool is None:
            self._cached_execute_tool = self._make_execute_tool()
        return self._cached_execute_tool

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search(
            query: Annotated[
                str,
                "Search query to find available tools",
            ],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> list[dict[str, Any]]:
            """Search for available tools by query.

            Returns matching tool definitions ranked by relevance,
            in the same format as list_tools.
            """
            tools = await transform.get_tool_catalog(ctx)
            results = await transform._search_transform._search(tools, query)
            return _serialize_tools_for_output(results)

        return Tool.from_function(fn=search, name=self.search_tool_name)

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
            ctx: Context = None,  # type: ignore[assignment]
        ) -> Any:
            """Execute tool calls using Python code."""
            defaults = transform._default_arguments
            # Cache the tool catalog for the duration of this execute block
            # so multiple call_tool() invocations don't each trigger list_tools().
            cached_tools: Sequence[Tool] | None = None

            async def _get_cached_tools() -> Sequence[Tool]:
                nonlocal cached_tools
                if cached_tools is None:
                    cached_tools = await transform.get_tool_catalog(ctx)
                return cached_tools

            async def call_tool(tool_name: str, params: dict[str, Any]) -> Any:
                backend_tools = await _get_cached_tools()
                tool = transform._find_tool(tool_name, backend_tools)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {tool_name}")

                accepted_args = set(tool.parameters.get("properties", {}).keys())
                skipped = {
                    key
                    for key in defaults
                    if key not in params and key not in accepted_args
                }
                if skipped:
                    logger.debug(
                        "default_arguments keys %s not accepted by tool %r, skipping",
                        skipped,
                        tool.name,
                    )
                merged = {
                    key: value
                    for key, value in defaults.items()
                    if key not in params and key in accepted_args
                }
                merged.update(params)

                result = await ctx.fastmcp.call_tool(tool.name, merged)
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


__all__ = [
    "CodeMode",
    "MontySandboxProvider",
    "SandboxProvider",
]
