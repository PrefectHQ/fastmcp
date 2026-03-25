"""GenerativeUI — a Provider that adds LLM-generated UI capabilities.

Registers a ``generate_ui`` tool that accepts Python code from the LLM,
executes it in a Pyodide sandbox, and renders the result as a Prefab app.
Also registers a ``components`` tool for the LLM to search the Prefab
component library, and the generative renderer resource with full CSP.

Usage::

    from fastmcp import FastMCP
    from fastmcp.apps.generative import GenerativeUI

    mcp = FastMCP("My Server")
    mcp.add_provider(GenerativeUI())
"""

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

GENERATIVE_URI = "ui://prefab/generative.html"


def _get_csp() -> ResourceCSP:
    """Build CSP from the generative renderer's requirements."""
    try:
        from prefab_ui.renderer import get_generative_renderer_csp

        csp = get_generative_renderer_csp()
        return ResourceCSP(
            resource_domains=csp.get("resource_domains"),
            connect_domains=csp.get("connect_domains"),
        )
    except ImportError:
        return ResourceCSP(
            resource_domains=["https://cdn.jsdelivr.net"],
            connect_domains=[
                "https://cdn.jsdelivr.net",
                "https://pypi.org",
                "https://files.pythonhosted.org",
            ],
        )


# ---------------------------------------------------------------------------
# Component introspection for the search tool
# ---------------------------------------------------------------------------


def _get_all_components() -> dict[str, type]:
    """Discover all Prefab components with their types."""
    import prefab_ui.components
    import prefab_ui.components.charts
    from prefab_ui.components.base import Component

    result: dict[str, type] = {}
    for module in (prefab_ui.components, prefab_ui.components.charts):
        names = getattr(module, "__all__", None) or [
            n for n in dir(module) if not n.startswith("_")
        ]
        for name in names:
            obj = getattr(module, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Component)
                and obj is not Component
            ):
                result[name] = obj
    return result


def _describe_component(name: str, cls: type) -> str:
    """One-line summary + fields for a component."""
    from prefab_ui.components.base import ContainerComponent, StatefulMixin

    parts = [name]

    tags: list[str] = []
    if issubclass(cls, ContainerComponent):
        tags.append("container")
    if issubclass(cls, StatefulMixin):
        tags.append("stateful")
    if tags:
        parts[0] += f" ({', '.join(tags)})"

    parts.append(f"  from {cls.__module__} import {name}")

    doc = (cls.__doc__ or "").strip().split("\n")[0]
    if doc:
        parts.append(f"  {doc}")

    for field_name, info in cls.model_fields.items():
        if field_name in ("type", "css_class", "id", "children", "let"):
            continue
        desc = info.description or ""
        anno = str(info.annotation).replace("typing.", "")
        line = f"  {field_name}: {anno}"
        if desc:
            line += f" — {desc[:80]}"
        parts.append(line)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# GenerativeUI Provider
# ---------------------------------------------------------------------------


class GenerativeUI(Provider):
    """A Provider that adds generative UI capabilities to a server.

    Registers:
    - A ``generate_ui`` tool that accepts Prefab Python code from the LLM,
      executes it in a Pyodide sandbox, and returns the rendered PrefabApp.
    - A ``components`` tool that lets the LLM search the Prefab component
      library for available components and their APIs.
    - The generative renderer resource (``ui://prefab/generative.html``)
      with CSP configured for Pyodide CDN access.

    The generative renderer supports streaming: as the LLM writes code
    into the ``code`` argument, the host forwards partial arguments to
    the already-running app via ``ontoolinputpartial``. The app executes
    partial code in browser-side Pyodide and renders progressively.

    Example::

        from fastmcp import FastMCP
        from fastmcp.apps.generative import GenerativeUI

        mcp = FastMCP("My Server")
        mcp.add_provider(GenerativeUI())
    """

    def __init__(
        self,
        *,
        tool_name: str = "generate_ui",
        include_components_tool: bool = True,
        components_tool_name: str = "components",
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

    def _ensure_setup(self) -> None:
        """Lazily register tools and resources on first access."""
        if self._setup_done:
            return
        self._setup_done = True

        csp = _get_csp()
        app_config = AppConfig(resource_uri=GENERATIVE_URI, csp=csp)

        # Register the generate_ui tool
        from prefab_ui.app import PrefabApp

        async def generate_ui(
            code: str,
            data: str | dict[str, Any] | None = None,
        ) -> PrefabApp:
            sandbox = self._get_sandbox()

            if isinstance(data, str):
                data = json.loads(data) if data.strip() else None

            try:
                wire = await sandbox.run(code, data=data)
            except RuntimeError as exc:
                raise ValueError(f"Code execution failed: {exc}") from exc

            return PrefabApp.from_json(wire)

        tool = Tool.from_function(
            generate_ui,
            name=self._tool_name,
            description=_GENERATE_UI_DESCRIPTION,
            meta={"ui": app_config_to_meta_dict(app_config)},
        )
        self._local._add_component(tool)

        # Register the components search tool
        if self._include_components_tool:
            all_components = _get_all_components()

            def components(query: str = "") -> str:
                q = query.lower()
                matches = {
                    name: cls
                    for name, cls in all_components.items()
                    if not q or q in name.lower()
                }
                if not matches:
                    return f"No components matching '{query}'. Try a broader search."
                sections = [
                    _describe_component(name, cls)
                    for name, cls in sorted(matches.items())
                ]
                header = (
                    f"{len(matches)} components"
                    if not q
                    else f"{len(matches)} components matching '{query}'"
                )
                return f"{header}:\n\n" + "\n\n".join(sections)

            components_tool = Tool.from_function(
                components,
                name=self._components_tool_name,
                description=(
                    "Search the Prefab component library. Returns component "
                    "names, import paths, descriptions, and their fields. "
                    "Pass a query to filter by name, or leave empty for "
                    "the full catalog."
                ),
            )
            self._local._add_component(components_tool)

        # Register the generative renderer resource
        from fastmcp.resources.types import TextResource

        try:
            from prefab_ui.renderer import get_generative_renderer_html

            renderer_html = get_generative_renderer_html()
        except ImportError:
            logger.error(
                "prefab-ui generative renderer not available. "
                "Install with: pip install 'fastmcp[apps]'"
            )
            return

        resource_config = AppConfig(csp=csp)
        resource = TextResource(
            uri=GENERATIVE_URI,  # type: ignore[arg-type]
            name="Prefab Generative Renderer",
            text=renderer_html,
            mime_type=UI_MIME_TYPE,
            meta={"ui": app_config_to_meta_dict(resource_config)},
        )
        self._local._add_component(resource)

    def _get_sandbox(self) -> Any:
        """Lazily create the Pyodide sandbox."""
        if self._sandbox is None:
            from prefab_ui.sandbox import Sandbox

            self._sandbox = Sandbox()
        return self._sandbox

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


_GENERATE_UI_DESCRIPTION = """Execute Prefab Python code in a sandbox and render the result.

The code runs in a Pyodide WASM sandbox with full Python support.
You must import everything you use. Use the `components` tool to
look up available components and their correct import paths.

Key patterns:

1. Build trees with context managers. Assign the root with `as`:

```python
from prefab_ui.components import Column, Heading, Text, Row, Badge
from prefab_ui.app import PrefabApp

with Column(gap=4) as view:
    Heading("Dashboard")
    with Row(gap=2):
        Text("Revenue: $1.2M")
        Badge("On Track", variant="success")

app = PrefabApp(view=view)
```

2. For interactive UIs, use stateful components and .rx for
   reactive bindings:

```python
from prefab_ui.components import Column, Slider, Text
from prefab_ui.app import PrefabApp

with Column(gap=4) as view:
    slider = Slider(value=50, min=0, max=100, name="threshold")
    Text(f"Threshold: {slider.rx}%")

app = PrefabApp(view=view, state={"threshold": 50})
```

3. Charts are in prefab_ui.components.charts:

```python
from prefab_ui.components.charts import BarChart, ChartSeries

BarChart(
    data=[{"month": "Jan", "rev": 100}, {"month": "Feb", "rev": 200}],
    series=[ChartSeries(data_key="rev", label="Revenue")],
    x_axis="month",
)
```

4. Data values passed via the `data` parameter are available as
   global variables in your code. Use Python freely — loops,
   f-strings, computation, list comprehensions all work.
"""
