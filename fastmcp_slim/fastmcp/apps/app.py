"""FastMCPApp — a Provider that represents a composable MCP application.

FastMCPApp binds entry-point tools (model calls these) together with backend
tools (the UI calls these via CallTool).  Backend tools are tagged with
``meta["fastmcp"]["app"]`` so they can be found through the provider chain
even when transforms (namespace, visibility, etc.) have renamed or hidden
them — the server sets a context var that tells ``Provider.get_tool`` to
fall back to a direct lookup for app-visible tools.

Usage::

    from fastmcp import FastMCP, FastMCPApp

    app = FastMCPApp("Dashboard")

    @app.ui()
    def show_dashboard() -> Component:
        return Column(...)

    @app.tool()
    def save_contact(name: str, email: str) -> str:
        return name

    server = FastMCP("Platform")
    server.add_provider(app)
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

from mcp.types import AnyFunction, Icon, ToolAnnotations

from fastmcp.server.providers.base import Provider
from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.providers.local_provider import LocalProvider
    from fastmcp.tools.base import Tool

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# CallTool resolver
# ---------------------------------------------------------------------------


def _make_resolver(app_name: str | None = None) -> Any:
    """Create a CallTool resolver that prefixes tool names with a hash.

    Structurally identical to the old ``___`` resolver — ``app_name`` is
    the FastMCPApp's name, known at serialization time from the tool's
    ``meta["fastmcp"]["app"]`` tag. The only change is the wire format:
    ``<hash>_<local_name>`` instead of ``<app_name>___<local_name>``.

    The dispatcher recognizes the hashed form and routes it via
    ``get_tool_by_hash`` which walks the provider tree recursively —
    same pattern as ``get_app_tool``.
    """
    from fastmcp.server.providers.addressing import (
        hashed_backend_name,
        parse_hashed_backend_name,
    )

    def _prefix(local_name: str) -> str:
        if app_name:
            # Don't re-hash an already-addressed name (same guard the
            # old ___ resolver had with "___" not in name).
            if parse_hashed_backend_name(local_name) is not None:
                return local_name
            return hashed_backend_name(app_name, local_name)
        return local_name

    def _resolve_tool_ref(fn: Any) -> Any:
        from prefab_ui.app import ResolvedTool

        if isinstance(fn, str):
            return ResolvedTool(name=_prefix(fn))

        fmeta: Any = None
        try:
            from fastmcp.decorators import get_fastmcp_meta

            fmeta = get_fastmcp_meta(fn)
        except Exception:
            pass

        if fmeta is not None:
            name: str | None = getattr(fmeta, "name", None)
            if name is not None:
                return ResolvedTool(name=_prefix(name))

        fn_name = getattr(fn, "__name__", None)
        if fn_name is not None:
            return ResolvedTool(name=_prefix(fn_name))

        raise ValueError(f"Cannot resolve tool reference: {fn!r}")

    return _resolve_tool_ref


def _dispatch_decorator(
    name_or_fn: str | AnyFunction | None,
    name: str | None,
    register: Callable[[Any, str | None], Any],
    decorator_name: str,
) -> Any:
    """Shared dispatch logic for @app.tool() and @app.ui() calling patterns."""
    if inspect.isroutine(name_or_fn):
        return register(name_or_fn, name)

    if isinstance(name_or_fn, str):
        if name is not None:
            raise TypeError(
                "Cannot specify both a name as first argument and as keyword argument."
            )
        tool_name: str | None = name_or_fn
    elif name_or_fn is None:
        tool_name = name
    else:
        raise TypeError(
            f"First argument to @{decorator_name} must be a function, string, or None, "
            f"got {type(name_or_fn)}"
        )

    def decorator(fn: F) -> F:
        return register(fn, tool_name)

    return decorator


# ---------------------------------------------------------------------------
# FastMCPApp
# ---------------------------------------------------------------------------


class FastMCPApp(Provider):
    """A Provider that represents an MCP application.

    Binds together entry-point tools (``@app.ui``), backend tools
    (``@app.tool``), and the Prefab renderer resource.  Backend tools
    are tagged with ``meta["fastmcp"]["app"]`` so ``Provider.get_tool``
    can find them by original name even when transforms have been applied.
    """

    def __init__(self, name: str) -> None:
        from fastmcp.server.providers.local_provider import LocalProvider

        super().__init__()
        self.name = name
        self._local: LocalProvider = LocalProvider(on_duplicate="error")

    def __repr__(self) -> str:
        return f"FastMCPApp({self.name!r})"

    # ------------------------------------------------------------------
    # @app.tool() — backend tools called by the UI
    # ------------------------------------------------------------------

    @overload
    def tool(
        self,
        name_or_fn: F,
        *,
        name: str | None = None,
        description: str | None = None,
        model: bool = False,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> F: ...

    @overload
    def tool(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        model: bool = False,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> Callable[[F], F]: ...

    def tool(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        model: bool = False,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Register a backend tool that the UI calls via CallTool.

        Backend tools default to ``visibility=["app"]``.  Pass ``model=True``
        to also expose the tool to the model (``visibility=["app", "model"]``).

        Supports multiple calling patterns::

            @app.tool
            def save(name: str): ...

            @app.tool()
            def save(name: str): ...

            @app.tool("custom_name")
            def save(name: str): ...
        """
        visibility: list[Literal["app", "model"]] = (
            ["app", "model"] if model else ["app"]
        )

        def _register(fn: F, tool_name: str | None) -> F:
            from fastmcp.tools.base import Tool

            resolved_name = tool_name or getattr(fn, "__name__", None)
            if resolved_name is None:
                raise ValueError(f"Cannot determine tool name for {fn!r}")

            from fastmcp.apps.config import AppConfig, app_config_to_meta_dict
            from fastmcp.server.providers.addressing import hash_tool

            app_config = AppConfig(visibility=visibility)
            meta: dict[str, Any] = {
                "ui": app_config_to_meta_dict(app_config),
                "fastmcp": {
                    "app": self.name,
                    "_tool_hash": hash_tool(self.name, resolved_name),
                },
            }

            tool_obj = Tool.from_function(
                fn,
                name=resolved_name,
                description=description,
                meta=meta,
                timeout=timeout,
                auth=auth,
            )
            self._local._add_component(tool_obj)
            return fn

        return _dispatch_decorator(name_or_fn, name, _register, "tool")

    # ------------------------------------------------------------------
    # @app.ui() — entry-point tools the model calls to open the app
    # ------------------------------------------------------------------

    @overload
    def ui(
        self,
        name_or_fn: F,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        tags: set[str] | None = None,
        icons: list[Icon] | None = None,
        annotations: ToolAnnotations | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> F: ...

    @overload
    def ui(
        self,
        name_or_fn: str | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        tags: set[str] | None = None,
        icons: list[Icon] | None = None,
        annotations: ToolAnnotations | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> Callable[[F], F]: ...

    def ui(
        self,
        name_or_fn: str | AnyFunction | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        tags: set[str] | None = None,
        icons: list[Icon] | None = None,
        annotations: ToolAnnotations | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Register a UI entry-point tool that the model calls.

        Entry-point tools default to ``visibility=["model"]`` and auto-wire
        the Prefab renderer resource and CSP. They are tagged with the app
        name so structured content includes ``_meta.fastmcp.app``.

        Supports multiple calling patterns::

            @app.ui
            def dashboard() -> Component: ...

            @app.ui()
            def dashboard() -> Component: ...

            @app.ui("my_dashboard")
            def dashboard() -> Component: ...
        """

        def _register(fn: F, tool_name: str | None) -> F:
            from fastmcp.apps.config import AppConfig, app_config_to_meta_dict
            from fastmcp.server.providers.addressing import hash_tool
            from fastmcp.server.providers.local_provider.decorators.tools import (
                PREFAB_RENDERER_URI,
            )
            from fastmcp.tools.base import Tool

            resolved = tool_name or getattr(fn, "__name__", None) or "unknown"
            app_config = AppConfig(
                resource_uri=PREFAB_RENDERER_URI,
                visibility=["model"],
            )

            meta: dict[str, Any] = {
                "ui": app_config_to_meta_dict(app_config),
                "fastmcp": {
                    "app": self.name,
                    "_tool_hash": hash_tool(self.name, resolved),
                },
            }

            tool_obj = Tool.from_function(
                fn,
                name=tool_name,
                description=description,
                title=title,
                tags=tags,
                icons=icons,
                annotations=annotations,
                meta=meta,
                timeout=timeout,
                auth=auth,
            )
            self._local._add_component(tool_obj)

            return fn

        return _dispatch_decorator(name_or_fn, name, _register, "ui")

    # ------------------------------------------------------------------
    # Programmatic tool addition
    # ------------------------------------------------------------------

    def add_tool(
        self,
        tool: Tool | Callable[..., Any],
    ) -> Tool:
        """Add a tool to this app programmatically.

        The tool is tagged with this app's name for routing.
        """
        from fastmcp.tools.base import Tool

        if not isinstance(tool, Tool):
            tool = Tool._ensure_tool(tool)

        from fastmcp.server.providers.addressing import hash_tool

        meta = dict(tool.meta) if tool.meta else {}
        fm = meta.setdefault("fastmcp", {})
        fm["app"] = self.name
        fm["_tool_hash"] = hash_tool(self.name, tool.name)
        ui = meta.setdefault("ui", {})
        if "visibility" not in ui:
            ui["visibility"] = ["app"]
        tool.meta = meta

        self._local._add_component(tool)
        return tool

    # ------------------------------------------------------------------
    # Provider interface — delegate to internal LocalProvider
    # ------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        return await self._local._list_tools()

    async def _get_tool(self, name: str, version: Any = None) -> Tool | None:
        return await self._local._get_tool(name, version)

    async def _list_resources(self) -> Sequence[Any]:
        return await self._local._list_resources()

    async def _get_resource(self, uri: str, version: Any = None) -> Any | None:
        return await self._local._get_resource(uri, version)

    async def _list_resource_templates(self) -> Sequence[Any]:
        return await self._local._list_resource_templates()

    async def _get_resource_template(self, uri: str, version: Any = None) -> Any | None:
        return await self._local._get_resource_template(uri, version)

    async def _list_prompts(self) -> Sequence[Any]:
        return await self._local._list_prompts()

    async def _get_prompt(self, name: str, version: Any = None) -> Any | None:
        return await self._local._get_prompt(name, version)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self._local.lifespan():
            yield

    # ------------------------------------------------------------------
    # Convenience runner
    # ------------------------------------------------------------------

    def run(
        self,
        transport: Literal["stdio", "http", "sse", "streamable-http"] | None = None,
        **kwargs: Any,
    ) -> None:
        """Create a temporary FastMCP server and run this app standalone."""
        from fastmcp.server.server import FastMCP

        server = FastMCP(self.name)
        server.add_provider(self)
        server.run(transport=transport, **kwargs)
