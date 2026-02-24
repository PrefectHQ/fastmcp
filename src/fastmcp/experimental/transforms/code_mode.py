from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Protocol

from mcp.types import TextContent
from pydantic import Field

from fastmcp.exceptions import NotFoundError
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec, version_sort_key


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


class SandboxProvider(Protocol):
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
        server: Any,
        *,
        default_arguments: dict[str, Any] | None = None,
        sandbox_provider: SandboxProvider | None = None,
        search_tool_name: str = "search",
        execute_tool_name: str = "execute",
        search_description: str | None = None,
        execute_description: str | None = None,
    ) -> None:
        if search_tool_name == execute_tool_name:
            raise ValueError("search_tool_name and execute_tool_name must be different.")

        self._server = server
        self._default_arguments = default_arguments or {}
        self._helpers: dict[str, Callable[..., Any]] = {}
        self._backend_tools: Sequence[Tool] = ()
        self.search_tool_name = search_tool_name
        self.execute_tool_name = execute_tool_name
        self.search_description = search_description
        self.execute_description = execute_description
        self.sandbox_provider = sandbox_provider or MontySandboxProvider()

    def search_helper(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a helper function available in search code."""
        self._helpers[fn.__name__] = fn
        return fn

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        self._backend_tools = [
            tool
            for tool in tools
            if tool.name not in {self.search_tool_name, self.execute_tool_name}
        ]
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

    async def _get_backend_tools(self) -> Sequence[Tool]:
        if self._backend_tools:
            raw_tools = self._backend_tools
        else:
            list_tools = getattr(self._server, "_list_tools", None)
            if list_tools is not None:
                raw_tools = await list_tools()
            else:
                raw_tools = await self._server.list_tools()

        backend_tools = [
            tool
            for tool in raw_tools
            if tool.name not in {self.search_tool_name, self.execute_tool_name}
        ]

        visible_tools: list[Tool] = []
        for tool in backend_tools:
            version = VersionSpec(eq=tool.version) if tool.version is not None else None
            resolved = await self._server.get_tool(tool.name, version=version)
            if resolved is None:
                continue
            if resolved.key != tool.key:
                continue
            visible_tools.append(resolved)
        return visible_tools

    async def _resolve_backend_tool(self, name: str) -> Tool | None:
        backend_tools = await self._get_backend_tools()

        for tool in backend_tools:
            if tool.key == name:
                return tool

        name_matches = [tool for tool in backend_tools if tool.name == name]
        if name_matches:
            return max(name_matches, key=version_sort_key)  # type: ignore[type-var]

        get_tool = getattr(self._server, "_get_tool", None)
        if get_tool is not None:
            return await get_tool(name)
        return await self._server.get_tool(name)

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
            ]
        ) -> Any:
            """Search for tools using Python code."""
            backend_tools = await transform._get_backend_tools()
            tool_dicts = [
                {
                    "name": tool.name,
                    "key": tool.key,
                    "description": tool.description or "",
                    "tags": dict.fromkeys(tool.tags, True) if tool.tags else None,
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
            ]
        ) -> Any:
            """Execute tool calls using Python code."""
            defaults = transform._default_arguments

            async def call_tool(tool_name_or_key: str, params: dict[str, Any]) -> Any:
                tool = await transform._resolve_backend_tool(tool_name_or_key)
                if tool is None:
                    raise NotFoundError(f"Unknown tool: {tool_name_or_key}")

                accepted_args = set(tool.parameters.get("properties", {}).keys())
                merged = {
                    key: value
                    for key, value in defaults.items()
                    if key not in params and value is not None and key in accepted_args
                }
                merged.update(params)

                version = VersionSpec(eq=tool.version) if tool.version is not None else None
                result = await transform._server.call_tool(
                    tool.name,
                    merged,
                    version=version,
                )
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

                texts = [
                    content.text
                    for content in result.content
                    if isinstance(content, TextContent)
                ]
                if len(texts) == 1:
                    return texts[0]
                return texts

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
