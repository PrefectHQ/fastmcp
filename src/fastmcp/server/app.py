"""FastMCPApp — a Provider that represents a composable MCP application.

FastMCPApp binds entry-point tools (model calls these) together with backend
tools (the UI calls these via CallTool). Backend tools get global keys —
UUID-suffixed stable identifiers that survive namespace transforms when
servers are composed — so ``CallTool(save_contact)`` keeps working even when
the app is mounted under a namespace.

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
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal, TypeVar, overload

from mcp.types import AnyFunction, Icon, ToolAnnotations

from fastmcp.decorators import get_fastmcp_meta
from fastmcp.server.auth.authorization import AuthCheck
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools.tool import Tool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Process-level registries
# ---------------------------------------------------------------------------
# Global key → Tool object.  FastMCP.call_tool checks this before normal
# provider resolution so that CallTool("save_contact-a1b2c3d4") reaches the
# right tool regardless of namespace transforms.
_APP_TOOL_REGISTRY: dict[str, Tool] = {}

# id(original_fn) → global key.  Used by the CallTool callable resolver to
# translate ``CallTool(save_contact)`` → ``"save_contact-a1b2c3d4"``.
_FN_TO_GLOBAL_KEY: dict[int, str] = {}


def get_global_tool(name: str) -> Tool | None:
    """Look up a tool by its global key, or return None."""
    return _APP_TOOL_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Global key helpers
# ---------------------------------------------------------------------------


def _make_global_key(name: str) -> str:
    """Generate a global key: ``{name}-{8_hex_chars}``."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _register_global_key(tool: Tool, fn: Any, global_key: str) -> None:
    """Register a tool in both process-level registries."""
    _APP_TOOL_REGISTRY[global_key] = tool
    _FN_TO_GLOBAL_KEY[id(fn)] = global_key


def _stamp_global_key(tool: Tool, global_key: str) -> None:
    """Write the global key into the tool's ``meta["ui"]["globalKey"]``."""
    meta = dict(tool.meta) if tool.meta else {}
    ui = dict(meta.get("ui", {})) if isinstance(meta.get("ui"), dict) else {}
    ui["globalKey"] = global_key
    meta["ui"] = ui
    tool.meta = meta


# ---------------------------------------------------------------------------
# CallTool callable resolver
# ---------------------------------------------------------------------------


def _resolve_tool_ref(fn: Any) -> Any:
    """Resolve a callable to a ``ResolvedTool`` for CallTool serialization.

    Always returns a ``ResolvedTool`` with the resolved name and any
    metadata the renderer needs (e.g. ``unwrap_result``).

    Resolution order:
    1. Global key registry (FastMCPApp tools) — includes metadata
    2. ``__fastmcp__`` metadata (decorated but not on a FastMCPApp)
    3. ``fn.__name__`` (bare function — works for standalone servers)
    """
    from prefab_ui.app import ResolvedTool  # ty: ignore[unresolved-import]

    global_key = _FN_TO_GLOBAL_KEY.get(id(fn))
    if global_key is not None:
        tool = _APP_TOOL_REGISTRY.get(global_key)
        unwrap = bool(
            tool is not None
            and tool.output_schema
            and tool.output_schema.get("x-fastmcp-wrap-result")
        )
        return ResolvedTool(name=global_key, unwrap_result=unwrap)

    fmeta = get_fastmcp_meta(fn)
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
    (``@app.tool``), the Prefab renderer resource, and global-key
    infrastructure so that composed/namespaced servers can still reach
    backend tools by stable identifiers.
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

        Backend tools get a global key for composition safety and default
        to ``visibility=["app"]``.  Pass ``model=True`` to also expose the
        tool to the model (``visibility=["app", "model"]``).

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

            global_key = _make_global_key(resolved_name)
            app_config = AppConfig(visibility=visibility)
            meta: dict[str, Any] = {"ui": app_config_to_meta_dict(app_config)}
            meta["ui"]["globalKey"] = global_key

            tool_obj = Tool.from_function(
                fn,
                name=resolved_name,
                description=description,
                meta=meta,
                timeout=timeout,
                auth=auth,
            )
            self._local._add_component(tool_obj)
            _register_global_key(tool_obj, fn, global_key)
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
        the Prefab renderer resource and CSP. They do NOT get a global key —
        the model resolves them through the normal transform chain.

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

            meta: dict[str, Any] = {"ui": app_config_to_meta_dict(app_config)}

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
        *,
        fn: Any | None = None,
    ) -> Tool:
        """Add a tool to this app programmatically.

        If the tool has ``meta["ui"]["globalKey"]``, it is assumed to already
        be configured (but still registered for lookup). Otherwise it is
        treated as a backend tool and gets a global key assigned automatically.

        Pass ``fn`` to register the original callable in the resolver so that
        ``CallTool(fn)`` can resolve to the global key.
        """
        if not isinstance(tool, Tool):
            fn = fn or tool
            tool = Tool._ensure_tool(tool)

        meta = tool.meta or {}
        ui = meta.get("ui", {})
        if isinstance(ui, dict) and "globalKey" in ui:
            global_key = ui["globalKey"]
        else:
            global_key = _make_global_key(tool.name)
            _stamp_global_key(tool, global_key)

        self._local._add_component(tool)

        _APP_TOOL_REGISTRY[global_key] = tool
        if fn is not None:
            _FN_TO_GLOBAL_KEY[id(fn)] = global_key

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
