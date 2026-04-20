"""Tests for the Search plugin.

These exercise the plugin-facing API (`Search`, its `Config`,
registration on a server) rather than the underlying transform
internals, which live in `tests/server/transforms/test_search.py`.
"""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from fastmcp import Client, FastMCP
from fastmcp.server.plugins.search import Search, SearchConfig
from fastmcp.server.plugins.search.bm25 import BM25SearchTransform
from fastmcp.server.plugins.search.regex import RegexSearchTransform


def _make_server_with_tools(plugins: list) -> FastMCP:
    mcp = FastMCP("t", plugins=plugins)

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    @mcp.tool
    def multiply(x: float, y: float) -> float:
        """Multiply two numbers."""
        return x * y

    @mcp.tool
    def search_files(pattern: str) -> list[str]:
        """Search the filesystem for files matching a pattern."""
        return []

    return mcp


class TestSearchPluginRegistration:
    async def test_default_plugin_uses_bm25_and_hides_tools(self):
        """With no config, Search uses BM25 and replaces list_tools output."""
        mcp = _make_server_with_tools([Search()])

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        # Only the synthetic pair should be visible.
        assert names == {"search_tools", "call_tool"}

    async def test_regex_strategy_dispatches_regex_transform(self):
        plugin = Search(SearchConfig(strategy="regex"))
        transforms = plugin.transforms()
        assert len(transforms) == 1
        assert isinstance(transforms[0], RegexSearchTransform)

    async def test_bm25_strategy_dispatches_bm25_transform(self):
        plugin = Search(SearchConfig(strategy="bm25"))
        transforms = plugin.transforms()
        assert len(transforms) == 1
        assert isinstance(transforms[0], BM25SearchTransform)

    async def test_always_visible_pins_tools_alongside_search_call(self):
        mcp = _make_server_with_tools([Search(SearchConfig(always_visible=["add"]))])

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert names == {"add", "search_tools", "call_tool"}

    async def test_custom_tool_names_apply(self):
        mcp = _make_server_with_tools(
            [Search(SearchConfig(search_tool_name="find", call_tool_name="invoke"))]
        )

        async with Client(mcp) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}

        assert names == {"find", "invoke"}

    async def test_search_binds_searchconfig_via_generic_parameter(self):
        """`Plugin[SearchConfig]` makes SearchConfig the validated config type."""
        assert Search._config_cls is SearchConfig

    async def test_dict_config_still_accepted(self):
        """Dict config path (inherited from Plugin base) constructs cleanly —
        used for loading plugin configs from JSON/YAML."""
        plugin = Search({"strategy": "regex"})
        assert isinstance(plugin.transforms()[0], RegexSearchTransform)

    async def test_hidden_tool_is_still_callable(self):
        """Search hides tools from list_tools but leaves them callable by name."""
        mcp = _make_server_with_tools([Search()])

        async with Client(mcp) as c:
            result = await c.call_tool("add", {"a": 2, "b": 3})
            assert result.data == 5


class TestSearchPluginConfigValidation:
    def test_unknown_strategy_rejected(self):
        with pytest.raises((ValidationError, Exception), match="strategy"):
            SearchConfig(strategy="fuzzy")  # ty: ignore[invalid-argument-type]

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            SearchConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta_name_and_version(self):
        """Search declares a stable meta so it's identifiable post-install."""
        assert Search.meta.name == "search"
        assert Search.meta.version == "0.1.0"


class TestDeprecationShim:
    """The old `fastmcp.server.transforms.search` path still works but warns."""

    def test_old_package_import_emits_deprecation_warning(self):
        # Force a fresh import so the module-level warning fires in this process.
        import importlib
        import sys

        sys.modules.pop("fastmcp.server.transforms.search", None)
        sys.modules.pop("fastmcp.server.transforms.search.base", None)
        sys.modules.pop("fastmcp.server.transforms.search.bm25", None)
        sys.modules.pop("fastmcp.server.transforms.search.regex", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.transforms.search")

        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any(
            "plugins.search" in str(w.message) for w in deprecations
        ), f"expected deprecation pointing at plugins.search, got {[str(w.message) for w in deprecations]}"

    def test_old_submodule_imports_still_resolve(self):
        """Existing code that imports from the old submodule path keeps working."""
        from fastmcp.server.transforms.search.bm25 import BM25SearchTransform as OldBM25
        from fastmcp.server.transforms.search.regex import (
            RegexSearchTransform as OldRegex,
        )

        # They're the same classes as the new path, not shims.
        assert OldBM25 is BM25SearchTransform
        assert OldRegex is RegexSearchTransform
