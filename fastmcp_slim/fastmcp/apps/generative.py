"""GenerativeUI — a Provider that adds LLM-generated UI capabilities.

Registers tools and resources from ``prefab_ui.generative`` so that an
LLM can write Prefab Python code, execute it in a sandbox, and render
the result as a streaming interactive UI.

Requires ``fastmcp[apps]`` (prefab-ui).

Usage::

    from fastmcp import FastMCP
    from fastmcp.apps.generative import GenerativeUI

    mcp = FastMCP("My Server")
    mcp.add_provider(GenerativeUI())
"""

try:
    import prefab_ui.generative as _gen
    from prefab_ui.renderer import (
        get_generative_renderer_csp,
        get_generative_renderer_html,
    )
except ImportError as _exc:
    raise ImportError(
        "GenerativeUI requires prefab-ui. Install with: pip install 'fastmcp[apps]'"
    ) from _exc

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastmcp.apps.config import AppConfig, ResourceCSP, app_config_to_meta_dict
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.local_provider import LocalProvider
from fastmcp.tools.base import Tool
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.mime import UI_MIME_TYPE

logger = get_logger(__name__)


def _build_csp() -> ResourceCSP:
    """Build CSP from the generative renderer's declared requirements."""
    csp = get_generative_renderer_csp()
    return ResourceCSP(
        resource_domains=csp.get("resource_domains"),
        connect_domains=csp.get("connect_domains"),
    )


class GenerativeUI(Provider):
    """A Provider that adds generative UI capabilities to a server.

    Registers:

    - A ``generate_ui`` tool that accepts Prefab Python code, executes
      it in a Pyodide sandbox, and returns the rendered PrefabApp.
      Supports streaming via ``ontoolinputpartial``.
    - A ``components`` tool that searches the Prefab component library.
    - The generative renderer resource with CSP for Pyodide CDN access.

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps.generative import GenerativeUI

        mcp = FastMCP("My Server")
        mcp.add_provider(GenerativeUI())
    """

    def __init__(
        self,
        *,
        tool_name: str = "generate_prefab_ui",
        include_components_tool: bool = True,
        components_tool_name: str = "search_prefab_components",
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._components_tool_name = components_tool_name
        self._include_components_tool = include_components_tool
        self._local = LocalProvider(on_duplicate="error")
        self._sandbox: Any = None
        self._setup_done = False

    def __repr__(self) -> str:
        return f"GenerativeUI(tool_name={self._tool_name!r})"

    def _get_sandbox(self) -> Any:
        """Lazily create the Pyodide sandbox."""
        if self._sandbox is None:
            from prefab_ui.sandbox import Sandbox

            self._sandbox = Sandbox()
        return self._sandbox

    def _ensure_setup(self) -> None:
        """Lazily register tools and resources on first access."""
        if self._setup_done:
            return

        csp = _build_csp()
        app_config = AppConfig(resource_uri=_gen.RESOURCE_URI, csp=csp)

        # -- generate_ui tool --
        # Wraps prefab_ui.generative.execute with sandbox lifecycle management.

        from prefab_ui.app import PrefabApp

        sandbox_ref = self  # capture for closure

        async def generate_ui(
            code: str,
            data: str | dict[str, Any] | None = None,
        ) -> PrefabApp:
            parsed_data: dict[str, Any] | None
            if isinstance(data, str):
                parsed_data = json.loads(data) if data.strip() else None
            else:
                parsed_data = data
            return await _gen.execute(
                code,
                data=parsed_data,
                sandbox=sandbox_ref._get_sandbox(),
            )

        tool = Tool.from_function(
            generate_ui,
            name=self._tool_name,
            description=_gen.execute.__doc__ or "",
            meta={"ui": app_config_to_meta_dict(app_config)},
        )
        self._local._add_component(tool)

        # -- components tool --

        if self._include_components_tool:
            components_tool = Tool.from_function(
                _gen.search_components,
                name=self._components_tool_name,
                description=_gen.search_components.__doc__ or "",
            )
            self._local._add_component(components_tool)

        # -- generative renderer resource --

        from fastmcp.resources.types import TextResource

        resource_config = AppConfig(csp=csp)
        resource = TextResource(
            uri=_gen.RESOURCE_URI,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            name="Prefab Generative Renderer",
            text=get_generative_renderer_html(),
            mime_type=UI_MIME_TYPE,
            meta={"ui": app_config_to_meta_dict(resource_config)},
        )
        self._local._add_component(resource)

        self._setup_done = True

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    async def _list_tools(self) -> Sequence[Tool]:
        self._ensure_setup()
        return await self._local._list_tools()

    async def _get_tool(self, name: str, version: Any = None) -> Tool | None:
        self._ensure_setup()
        return await self._local._get_tool(name, version)

    async def _list_resources(self) -> Sequence[Any]:
        self._ensure_setup()
        return await self._local._list_resources()

    async def _get_resource(self, uri: str, version: Any = None) -> Any | None:
        self._ensure_setup()
        return await self._local._get_resource(uri, version)

    async def _list_resource_templates(self) -> Sequence[Any]:
        return []

    async def _get_resource_template(self, uri: str, version: Any = None) -> Any | None:
        return None

    async def _list_prompts(self) -> Sequence[Any]:
        return []

    async def _get_prompt(self, name: str, version: Any = None) -> Any | None:
        return None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        self._ensure_setup()
        async with self._local.lifespan():
            yield
