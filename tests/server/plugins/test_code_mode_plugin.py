"""Tests for the CodeMode plugin wrapper.

These exercise the plugin-facing API (`CodeMode`, `CodeModeConfig`,
registration on a server) rather than the underlying transform
internals, which live in `tests/server/plugins/test_code_mode.py`.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.plugins.code_mode import (
    CodeMode,
    CodeModeConfig,
    GetSchemas,
    ListTools,
)
from fastmcp.server.plugins.code_mode.transform import CodeModeTransform


class _UnsafeTestSandboxProvider:
    """UNSAFE: Uses exec() for testing only. Never use in production."""

    async def run(self, code, *, inputs=None, external_functions=None):
        raise AssertionError("this sandbox should never actually run")


def _server_with_plugin(plugin: CodeMode) -> FastMCP:
    mcp = FastMCP("t", plugins=[plugin])

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @mcp.tool
    def multiply(x: float, y: float) -> float:
        """Multiply two numbers."""
        return x * y

    return mcp


class TestCodeModePluginRegistration:
    async def test_default_plugin_hides_catalog(self):
        """With no config, CodeMode replaces the catalog with search + execute."""
        mcp = _server_with_plugin(
            CodeMode(sandbox_provider=_UnsafeTestSandboxProvider())
        )

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        # Default discovery: Search + GetSchemas, plus execute.
        assert names == {"search", "get_schema", "execute"}

    async def test_custom_execute_tool_name(self):
        plugin = CodeMode(
            CodeModeConfig(execute_tool_name="run"),
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
        mcp = _server_with_plugin(plugin)

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert "run" in names
        assert "execute" not in names

    async def test_discovery_tools_override(self):
        """Passing discovery_tools replaces the default Search + GetSchemas set."""
        plugin = CodeMode(
            sandbox_provider=_UnsafeTestSandboxProvider(),
            discovery_tools=[ListTools(), GetSchemas()],
        )
        mcp = _server_with_plugin(plugin)

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert names == {"list_tools", "get_schema", "execute"}

    async def test_transforms_returns_codemode_transform(self):
        plugin = CodeMode(sandbox_provider=_UnsafeTestSandboxProvider())
        transforms = plugin.transforms()
        assert len(transforms) == 1
        assert isinstance(transforms[0], CodeModeTransform)

    async def test_config_generic_binding(self):
        """`Plugin[CodeModeConfig]` makes CodeModeConfig the validated config type."""
        assert CodeMode._config_cls is CodeModeConfig

    async def test_dict_config_accepted(self):
        """Dict config works for loading from JSON/YAML."""
        plugin = CodeMode(
            {"execute_tool_name": "go"},
            sandbox_provider=_UnsafeTestSandboxProvider(),
        )
        assert plugin.config.execute_tool_name == "go"

    async def test_hidden_tool_remains_callable(self):
        """CodeMode hides tools from list_tools but leaves them callable by name."""
        mcp = _server_with_plugin(
            CodeMode(sandbox_provider=_UnsafeTestSandboxProvider())
        )

        async with Client(mcp) as c:
            result = await c.call_tool("add", {"a": 2, "b": 3})
            assert result.data == 5


class TestCodeModeConfigValidation:
    def test_unknown_sandbox_rejected(self):
        with pytest.raises((ValidationError, Exception), match="sandbox"):
            CodeModeConfig(sandbox="docker")  # ty: ignore[invalid-argument-type]

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            CodeModeConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta(self):
        """CodeMode relies on Plugin's auto-derived meta: kebab-cased class
        name, no independent version (bundled first-party plugin)."""
        assert CodeMode.meta.name == "code-mode"
        assert CodeMode.meta.version is None


class TestDeprecationShim:
    """The old `fastmcp.experimental.transforms.code_mode` path still works but warns."""

    def test_old_package_import_emits_deprecation_warning(self):
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop("fastmcp.experimental.transforms.code_mode", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.experimental.transforms.code_mode")

        fastmcp_deprecations = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert any(
            "plugins.code_mode" in str(w.message) for w in fastmcp_deprecations
        ), (
            f"expected FastMCPDeprecationWarning pointing at plugins.code_mode, "
            f"got {[(w.category.__name__, str(w.message)) for w in caught]}"
        )

    def test_old_imports_still_resolve(self):
        """Existing code that imports from the old path keeps working."""
        from fastmcp.exceptions import FastMCPDeprecationWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastMCPDeprecationWarning)
            from fastmcp.experimental.transforms.code_mode import (
                CodeMode as OldCodeMode,
            )
            from fastmcp.experimental.transforms.code_mode import (
                CodeModeTransform as OldTransform,
            )

        # CodeMode is now the plugin class, not the transform — advertised
        # behavior of the shim.
        assert OldCodeMode is CodeMode
        assert OldTransform is CodeModeTransform
