"""Tests for the CodeMode plugin wrapper.

Transform behavior (what `CodeModeTransform` does to the catalog, how
discovery tools render, sandbox execution, etc.) is covered by
`test_code_mode.py` and `test_code_mode_discovery.py`. This file only
covers the plugin layer itself — config validation, meta derivation,
dict-config coercion, and the deprecation shim at the old import path.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from fastmcp.server.plugins.code_mode import CodeMode, CodeModeConfig


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

    def test_old_codemode_is_still_the_transform(self):
        """`CodeMode` at the old path keeps pointing at the transform so
        that existing `mcp.add_transform(CodeMode())` code keeps working.
        The new plugin class lives at the new path only."""
        from fastmcp.exceptions import FastMCPDeprecationWarning
        from fastmcp.server.plugins.code_mode.transform import CodeModeTransform

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastMCPDeprecationWarning)
            from fastmcp.experimental.transforms.code_mode import (
                CodeMode as OldCodeMode,
            )
            from fastmcp.experimental.transforms.code_mode import (
                CodeModeTransform as OldTransform,
            )

        assert OldCodeMode is CodeModeTransform
        assert OldTransform is CodeModeTransform
        # The plugin class is NOT the same as the old-path `CodeMode`:
        assert OldCodeMode is not CodeMode
