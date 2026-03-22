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
    def save_contact(name: str, email: str) -> dict:
        return {"name": name, "email": email}

    server = FastMCP("Platform")
    server.add_provider(app)
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal, TypeVar, overload

from mcp.types import AnyFunction, Icon, ToolAnnotations

from fastmcp.server.auth.authorization import AuthCheck
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# CallTool resolver
# ---------------------------------------------------------------------------


def _resolve_tool_ref(fn: Any) -> Any:
    """Resolve a callable or string to a ``ResolvedTool`` for CallTool serialization.

    For strings, passes them through as-is — the server resolves them at
    call time using ``_meta.fastmcp.app``.

    For callables, extracts the tool name from ``__fastmcp__`` metadata
    or ``__name__``.
    """
    from prefab_ui.app import ResolvedTool

    if isinstance(fn, str):
        return ResolvedTool(name=fn)

    fmeta: Any = None
    try:
        from fastmcp.decorators import get_fastmcp_meta

        fmeta = get_fastmcp_meta(fn)
    except Exception:
        pass

    if fmeta is not None:
        name: str | None = getattr(fmeta, "name", None)
        if name is not None:
            return ResolvedTool(name=name)

    fn_name = getattr(fn, "__name__", None)
    if fn_name is not None:
        return ResolvedTool(name=fn_name)

    raise ValueError(f"Cannot resolve tool reference: {fn!r}")


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
        super().__init__()
        self.name = name
        self._local = LocalProvider(on_duplicate="error")

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
            resolved_name = tool_name or getattr(fn, "__name__", None)
            if resolved_name is None:
                raise ValueError(f"Cannot determine tool name for {fn!r}")

            from fastmcp.server.apps import AppConfig, app_config_to_meta_dict

            app_config = AppConfig(visibility=visibility)
            meta: dict[str, Any] = {
                "ui": app_config_to_meta_dict(app_config),
                "fastmcp": {"app": self.name},
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
            from fastmcp.server.apps import AppConfig, app_config_to_meta_dict
            from fastmcp.server.providers.local_provider.decorators.tools import (
                PREFAB_RENDERER_URI,
                _ensure_prefab_renderer,
            )

            try:
                from prefab_ui.renderer import get_renderer_csp

                from fastmcp.server.apps import ResourceCSP

                csp = get_renderer_csp()
                app_config = AppConfig(
                    resource_uri=PREFAB_RENDERER_URI,
                    visibility=["model"],
                    csp=ResourceCSP(
                        resource_domains=csp.get("resource_domains"),
                        connect_domains=csp.get("connect_domains"),
                    ),
                )
            except ImportError:
                app_config = AppConfig(
                    resource_uri=PREFAB_RENDERER_URI,
                    visibility=["model"],
                )

            meta: dict[str, Any] = {
                "ui": app_config_to_meta_dict(app_config),
                "fastmcp": {"app": self.name},
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

            # Register the Prefab renderer resource on the internal provider
            with suppress(ImportError):
                _ensure_prefab_renderer(self._local)

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
        if not isinstance(tool, Tool):
            tool = Tool._ensure_tool(tool)

        # Tag with app name for routing
        meta = dict(tool.meta) if tool.meta else {}
        meta.setdefault("fastmcp", {})["app"] = self.name
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
