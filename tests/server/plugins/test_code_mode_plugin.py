"""Tests for the CodeMode plugin wrapper.

Transform behavior (what `CodeModeTransform` does to the catalog, how
discovery tools render, sandbox execution, etc.) is covered by
`test_code_mode.py` and `test_code_mode_discovery.py`. This file only
covers the plugin layer itself — config validation, meta derivation,
dict-config coercion, and the deprecation shim at the old import path.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from fastmcp import FastMCP
from fastmcp.server.plugins.code_mode import CodeMode, CodeModeConfig


class _NoopSandbox:
    async def run(
        self,
        code: str,
        *,
        inputs: dict[str, Any] | None = None,
        external_functions: dict[str, Any] | None = None,
    ) -> Any:
        return None


class TestCodeModeConfig:
    def test_config_generic_binding(self):
        """`Plugin[CodeModeConfig]` binds CodeModeConfig as the validated config type."""
        assert CodeMode._config_cls is CodeModeConfig

    def test_dict_config_accepted(self):
        """Dict config works for loading from JSON/YAML."""
        plugin = CodeMode({"execute_tool_name": "go"})
        assert plugin.config.execute_tool_name == "go"

    def test_unknown_sandbox_rejected(self):
        with pytest.raises((ValidationError, Exception), match="sandbox"):
            CodeModeConfig(sandbox="docker")  # ty: ignore[invalid-argument-type]

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            CodeModeConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta(self):
        """CodeMode uses Plugin's auto-derived meta: kebab-cased class
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

    async def test_legacy_add_transform_pattern_still_works(self):
        """End-to-end: old `add_transform(CodeMode(...))` code keeps
        working. The point of the shim is that this doesn't break — the
        identity-check test alone wouldn't catch a regression where
        `CodeMode` at the old path drifted to the plugin class."""
        from fastmcp.exceptions import FastMCPDeprecationWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastMCPDeprecationWarning)
            from fastmcp.experimental.transforms.code_mode import (
                CodeMode as OldCodeMode,
            )

        mcp = FastMCP("legacy")

        @mcp.tool
        def ping() -> str:
            return "pong"

        mcp.add_transform(OldCodeMode(sandbox_provider=_NoopSandbox()))

        tools = await mcp.list_tools(run_middleware=False)
        assert {t.name for t in tools} == {"search", "get_schema", "execute"}
