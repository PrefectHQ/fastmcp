"""CodeMode plugin: tool execution via LLM-generated code.

`CodeMode` replaces the entire tool catalog with two classes of
meta-tool — discovery tools (search, get_schema, etc.) and a single
`execute` tool that runs LLM-generated Python in a sandbox. The model
discovers what's available on demand and chains calls inside one
sandboxed code block, which dramatically cuts round-trips and context
for servers with many tools.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from fastmcp.server.plugins.base import Plugin
from fastmcp.server.plugins.code_mode.discovery import DiscoveryToolFactory
from fastmcp.server.plugins.code_mode.sandbox import (
    MontySandboxProvider,
    SandboxProvider,
)
from fastmcp.server.plugins.code_mode.transform import CodeModeTransform
from fastmcp.server.transforms import Transform


class CodeModeConfig(BaseModel):
    """Config model for the `CodeMode` plugin.

    Only covers JSON-serializable settings — the sandbox provider and
    discovery-tool factories are passed through `CodeMode.__init__`
    directly because they're real Python objects.
    """

    model_config = ConfigDict(extra="forbid")

    sandbox: Literal["monty"] = "monty"
    """Built-in sandbox provider to use. `"monty"` uses
    `pydantic-monty`. For a custom provider, pass `sandbox_provider=...`
    to `CodeMode.__init__` instead."""

    sandbox_limits: dict[str, Any] | None = None
    """Resource limits for the default Monty sandbox. Keys:
    `max_duration_secs`, `max_allocations`, `max_memory`,
    `max_recursion_depth`, `gc_interval`. All optional."""

    execute_tool_name: str = "execute"
    """Name of the generated execute tool."""

    execute_description: str | None = None
    """Override the default description of the execute tool. `None`
    keeps the built-in guidance."""


class CodeMode(Plugin[CodeModeConfig]):
    """Collapse the tool catalog behind discovery + code-execution meta-tools.

    Users write a CodeMode-enabled server exactly like a normal server;
    the plugin takes care of hiding the real tools and exposing search
    / get_schema / execute in their place.

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.server.plugins.code_mode import CodeMode

        mcp = FastMCP("Server", plugins=[CodeMode()])
        ```

    For a custom sandbox or custom discovery-tool set, pass Python
    objects through `__init__`:

        ```python
        from fastmcp.server.plugins.code_mode import (
            CodeMode,
            CodeModeConfig,
            GetSchemas,
            ListTools,
        )

        mcp = FastMCP(
            "Server",
            plugins=[
                CodeMode(
                    CodeModeConfig(execute_tool_name="run"),
                    sandbox_provider=my_custom_sandbox,
                    discovery_tools=[ListTools(), GetSchemas()],
                )
            ],
        )
        ```
    """

    # `meta` is auto-derived (name="code-mode", version=None) — the right
    # answer for a bundled first-party plugin. Declare `meta` explicitly
    # (or use `PluginMeta.from_package(...)`) if published separately.

    def __init__(
        self,
        config: CodeModeConfig | dict[str, Any] | None = None,
        *,
        sandbox_provider: SandboxProvider | None = None,
        discovery_tools: list[DiscoveryToolFactory] | None = None,
    ) -> None:
        super().__init__(config)
        self._sandbox_override = sandbox_provider
        self._discovery_override = discovery_tools

    def transforms(self) -> list[Transform]:
        sandbox = self._sandbox_override or self._build_default_sandbox()
        return [
            CodeModeTransform(
                sandbox_provider=sandbox,
                discovery_tools=self._discovery_override,
                execute_tool_name=self.config.execute_tool_name,
                execute_description=self.config.execute_description,
            )
        ]

    def _build_default_sandbox(self) -> SandboxProvider:
        limits_dict = self.config.sandbox_limits
        if limits_dict is None:
            return MontySandboxProvider()

        # Defer the import so Monty is only a hard dependency when
        # `sandbox_limits` is actually configured.
        from pydantic_monty import ResourceLimits

        return MontySandboxProvider(limits=ResourceLimits(**limits_dict))
