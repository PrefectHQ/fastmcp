from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Annotated, Any, Protocol

from mcp.types import TextContent
from pydantic import Field

from fastmcp.exceptions import NotFoundError
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec, version_sort_key

logger = logging.getLogger(__name__)

# When True, CodeMode passes through in ``list_tools()`` instead of
# hiding tools.  This lets the search/execute tools call back into the
# server's ``list_tools()`` to get the auth-filtered catalog without
# recursively hiding everything behind the meta-tools.
_code_mode_bypass: ContextVar[bool] = ContextVar("_code_mode_bypass", default=False)


def _strip_circular(obj: Any, _seen: frozenset[int] = frozenset()) -> Any:
    """Deep-copy a JSON-like object, replacing circular references with None."""
    obj_id = id(obj)
    if obj_id in _seen:
        return None
    _seen = _seen | {obj_id}
    if isinstance(obj, dict):
        return {key: _strip_circular(value, _seen) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_strip_circular(value, _seen) for value in obj]
    return obj


def _ensure_async(fn: Callable[..., Any]) -> Callable[..., Any]:
    if asyncio.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


def _unwrap_tool_result(result: ToolResult, tool: Tool) -> Any:
    """Extract a Python-friendly value from a ToolResult."""
    if result.structured_content is not None:
        structured = result.structured_content
        wrap_result = bool(
            (tool.output_schema or {}).get("x-fastmcp-wrap-result")
        )
        if (
            wrap_result
            and isinstance(structured, dict)
            and set(structured) == {"result"}
        ):
            return structured["result"]
        return structured

    contents = []
    for content in result.content:
        if isinstance(content, TextContent):
            contents.append(content.text)
        else:
            contents.append(content.model_dump())
    if len(contents) == 1:
        return contents[0]
    return contents


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
    """Sandbox provider backed by `pydantic-monty`."""

    def __init__(self, *, install_hint: str = "fastmcp[monty]") -> None:
        self.install_hint = install_hint

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
                f"Install it with `{self.install_hint}` or pass a custom SandboxProvider."
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
        return await pydantic_monty.run_monty_async(monty, **run_kwargs)


class CodeMode(Transform):
    """Transform that collapses all tools into `search` + `execute` meta-tools."""

    def __init__(
        self,
        server: Any = None,
        *,
        default_arguments: dict[str, Any] | None = None,
        sandbox_provider: SandboxProvider | None = None,
        search_tool_name: str = "search",
        execute_tool_name: str = "execute",
        search_description: str | None = None,
        execute_description: str | None = None,
    ) -> None:
        if search_tool_name == execute_tool_name:
            raise ValueError(
                "search_tool_name and execute_tool_name must be different."
            )

        if server is not None:
            logger.warning(
                "Passing 'server' to CodeMode is deprecated and ignored. "
                "CodeMode now accesses the server via Context."
            )
        self._default_arguments = default_arguments or {}
        self._helpers: dict[str, Callable[..., Any]] = {}
        self.search_tool_name = search_tool_name
        self.execute_tool_name = execute_tool_name
        self.search_description = search_description
        self.execute_description = execute_description
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()

    def search_helper(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a helper function available in search code."""
        name = getattr(fn, "__name__", None)
        if name is None:
            raise TypeError(
                "search_helper requires a named function (must have __name__)"
            )
        self._helpers[name] = fn
        return fn

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if _code_mode_bypass.get():
            return tools

        meta_names = {self.search_tool_name, self.execute_tool_name}
        colliding = [tool.name for tool in tools if tool.name in meta_names]
        if colliding:
            logger.warning(
                "CodeMode is hiding backend tool(s) %s because they collide "
                "with meta-tool names. Use search_tool_name/execute_tool_name "
                "to choose different meta-tool names.",
                colliding,
            )
        return [self._make_search_tool(), self._make_execute_tool()]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        if name == self.search_tool_name:
            return self._make_search_tool()
        if name == self.execute_tool_name:
            return self._make_execute_tool()
        return await call_next(name, version=version)

    def _build_search_description(self) -> str:
        if self.search_description is not None:
            return self.search_description

        lines = [
            "Search tools with Python list comprehensions (async/await supported).",
            "Use `return` to produce output.",
            "Available in scope: `tools: list[dict]`.",
            "Tool dict fields: `name`, `key`, `description`, `tags`, `parameters`, `output_schema`.",
        ]
        if self._helpers:
            lines.append("")
            lines.append("Helpers:")
            for name, fn in self._helpers.items():
                doc = (fn.__doc__ or "").strip()
                lines.append(f"  - {name}(...) — {doc}")
        return "\n".join(lines)

    def _build_execute_description(self) -> str:
        if self.execute_description is not None:
            return self.execute_description

        return (
            "Chain `await call_tool(...)` calls in one Python block; prefer returning the final answer from a single block.\n"
            "Use `return` to produce output.\n"
            "Only `call_tool(tool_name_or_key: str, params: dict) -> Any` is available in scope."
        )

    async def _get_visible_tools(self, ctx: Context) -> Sequence[Tool]:
        """Get the auth-filtered tool catalog.

        Calls the server's ``list_tools()`` with a bypass flag so this
        transform passes through instead of hiding everything.  The rest
        of the pipeline — middleware, visibility, component auth — runs
        normally, so the result only contains tools the current user is
        authorized to see.
        """
        token = _code_mode_bypass.set(True)
        try:
            tools = await ctx.fastmcp.list_tools()
        finally:
            _code_mode_bypass.reset(token)
        meta_names = {self.search_tool_name, self.execute_tool_name}
        return [t for t in tools if t.name not in meta_names]

    async def _resolve_backend_tool(
        self, name: str, ctx: Context
    ) -> Tool | None:
        backend_tools = await self._get_visible_tools(ctx)

        for tool in backend_tools:
            if tool.key == name:
                return tool

        name_matches = [tool for tool in backend_tools if tool.name == name]
        if name_matches:
            return max(name_matches, key=version_sort_key)  # type: ignore[type-var]

        return None

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search(
            code: Annotated[
                str,
                Field(
                    description=(
                        "Python async code to search available tools and their schemas"
                    )
                ),
            ],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> Any:
            """Search for tools using Python code."""
            backend_tools = await transform._get_visible_tools(ctx)
            tool_dicts = [
                {
                    "name": tool.name,
                    "key": tool.key,
                    "description": tool.description or "",
                    "tags": sorted(tool.tags) if tool.tags else None,
                    "parameters": _strip_circular(tool.parameters),
                    "output_schema": _strip_circular(tool.output_schema),
                }
                for tool in backend_tools
            ]
            return await transform.sandbox_provider.run(
                code,
                inputs={"tools": tool_dicts},
                external_functions=dict(transform._helpers),
            )

        return Tool.from_function(
            fn=search,
            name=self.search_tool_name,
            description=self._build_search_description(),
        )

    def _make_execute_tool(self) -> Tool:
        transform = self

        async def execute(
            code: Annotated[
                str,
                Field(
                    description=(
                        "Python async code to execute tool calls via call_tool(name_or_key, arguments)"
                    )
                ),
            ],
            ctx: Context = None,  # type: ignore[assignment]
        ) -> Any:
            """Execute tool calls using Python code."""
            defaults = transform._default_arguments

            async def call_tool(tool_name_or_key: str, params: dict[str, Any]) -> Any:
                tool = await transform._resolve_backend_tool(tool_name_or_key, ctx)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {tool_name_or_key}")

                accepted_args = set(tool.parameters.get("properties", {}).keys())
                merged = {
                    key: value
                    for key, value in defaults.items()
                    if key not in params and key in accepted_args
                }
                merged.update(params)

                result = await ctx.fastmcp.call_tool(tool.name, merged)
                return _unwrap_tool_result(result, tool)

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
