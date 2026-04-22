"""Tests for the PromptsAsTools plugin wrapper.

Transform behavior is covered by `test_prompts_as_tools.py`. This file
only covers plugin-layer concerns — config validation, meta derivation,
and the deprecation shim at the old import path.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.plugins.prompts_as_tools import (
    PromptsAsTools,
    PromptsAsToolsConfig,
)


class TestPromptsAsToolsConfig:
    def test_config_generic_binding(self):
        assert PromptsAsTools._config_cls is PromptsAsToolsConfig

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            PromptsAsToolsConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta(self):
        assert PromptsAsTools.meta.name == "prompts-as-tools"
        assert PromptsAsTools.meta.version is None


class TestPromptsAsToolsPluginRegistration:
    async def test_plugin_registers_synthetic_tools(self):
        mcp = FastMCP("t", plugins=[PromptsAsTools()])

        @mcp.prompt
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello {name}"

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert {"list_prompts", "get_prompt"}.issubset(names)


class TestDeprecationShim:
    def test_old_path_emits_deprecation_warning(self):
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop("fastmcp.server.transforms.prompts_as_tools", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.transforms.prompts_as_tools")

        fastmcp_deprecations = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert any(
            "plugins.prompts_as_tools" in str(w.message) for w in fastmcp_deprecations
        ), (
            f"expected FastMCPDeprecationWarning pointing at plugins.prompts_as_tools, "
            f"got {[(w.category.__name__, str(w.message)) for w in caught]}"
        )

    async def test_legacy_add_transform_pattern_still_works(self):
        """End-to-end: old `add_transform(PromptsAsTools(mcp))` code keeps
        working. `PromptsAsTools` at the old path must remain the transform
        class, not the plugin."""
        from fastmcp.exceptions import FastMCPDeprecationWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastMCPDeprecationWarning)
            from fastmcp.server.transforms.prompts_as_tools import (
                PromptsAsTools as OldPromptsAsTools,
            )

        mcp = FastMCP("legacy")

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello {name}"

        mcp.add_transform(OldPromptsAsTools(mcp))

        tools = await mcp.list_tools(run_middleware=False)
        assert {"list_prompts", "get_prompt"}.issubset({t.name for t in tools})

    def test_top_level_import_does_not_emit_deprecation(self):
        """`from fastmcp.server.transforms import Transform` should not
        trigger a PromptsAsTools deprecation warning. The warning only
        fires when the leaf module is imported directly."""
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        # Flush anything that might carry a cached import.
        sys.modules.pop("fastmcp.server.transforms", None)
        sys.modules.pop("fastmcp.server.transforms.prompts_as_tools", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.transforms")
            # Access the attr via __getattr__, which should NOT load the
            # shim leaf module.
            mod = sys.modules["fastmcp.server.transforms"]
            _ = mod.Transform

        assert not any(
            issubclass(w.category, FastMCPDeprecationWarning)
            and "prompts_as_tools" in str(w.message)
            for w in caught
        ), (
            f"unexpected prompts_as_tools deprecation warning from top-level "
            f"import: {[(w.category.__name__, str(w.message)) for w in caught]}"
        )
