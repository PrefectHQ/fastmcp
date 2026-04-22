"""Tests for the ResourcesAsTools plugin wrapper.

Transform behavior is covered by `test_resources_as_tools.py`. This file
only covers plugin-layer concerns — config validation, meta derivation,
and the deprecation shim at the old import path.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.plugins.resources_as_tools import (
    ResourcesAsTools,
    ResourcesAsToolsConfig,
)


class TestResourcesAsToolsConfig:
    def test_config_generic_binding(self):
        assert ResourcesAsTools._config_cls is ResourcesAsToolsConfig

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            ResourcesAsToolsConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta(self):
        assert ResourcesAsTools.meta.name == "resources-as-tools"
        assert ResourcesAsTools.meta.version is None


class TestResourcesAsToolsPluginRegistration:
    async def test_plugin_registers_synthetic_tools(self):
        mcp = FastMCP("t", plugins=[ResourcesAsTools()])

        @mcp.resource("test://hello")
        def hello() -> str:
            return "world"

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert {"list_resources", "read_resource"}.issubset(names)


class TestDeprecationShim:
    def test_old_path_emits_deprecation_warning(self):
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop("fastmcp.server.transforms.resources_as_tools", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.transforms.resources_as_tools")

        fastmcp_deprecations = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert any(
            "plugins.resources_as_tools" in str(w.message)
            for w in fastmcp_deprecations
        ), (
            f"expected FastMCPDeprecationWarning pointing at plugins.resources_as_tools, "
            f"got {[(w.category.__name__, str(w.message)) for w in caught]}"
        )

    async def test_legacy_add_transform_pattern_still_works(self):
        from fastmcp.exceptions import FastMCPDeprecationWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FastMCPDeprecationWarning)
            from fastmcp.server.transforms.resources_as_tools import (
                ResourcesAsTools as OldResourcesAsTools,
            )

        mcp = FastMCP("legacy")

        @mcp.resource("test://hello")
        def hello() -> str:
            return "world"

        mcp.add_transform(OldResourcesAsTools(mcp))

        tools = await mcp.list_tools(run_middleware=False)
        assert {"list_resources", "read_resource"}.issubset({t.name for t in tools})
