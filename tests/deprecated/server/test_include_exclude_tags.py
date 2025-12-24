"""Tests for deprecated include_tags/exclude_tags parameters."""

import pytest

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool


class TestIncludeExcludeTagsDeprecation:
    """Test that include_tags/exclude_tags emit deprecation warnings but still work."""

    def test_exclude_tags_emits_warning(self):
        """exclude_tags parameter emits deprecation warning."""
        with pytest.warns(DeprecationWarning, match="exclude_tags.*deprecated"):
            FastMCP(exclude_tags={"internal"})

    def test_include_tags_emits_warning(self):
        """include_tags parameter emits deprecation warning."""
        with pytest.warns(DeprecationWarning, match="include_tags.*deprecated"):
            FastMCP(include_tags={"public"})

    def test_exclude_tags_still_works(self):
        """exclude_tags still filters components correctly."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(exclude_tags={"internal"})

        tool_public = Tool(name="public_tool", parameters={}, tags={"public"})
        tool_internal = Tool(name="internal_tool", parameters={}, tags={"internal"})

        assert mcp._is_component_enabled(tool_public) is True
        assert mcp._is_component_enabled(tool_internal) is False

    def test_include_tags_still_works(self):
        """include_tags still filters components correctly."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"})

        tool_public = Tool(name="public_tool", parameters={}, tags={"public"})
        tool_other = Tool(name="other_tool", parameters={}, tags={"other"})

        assert mcp._is_component_enabled(tool_public) is True
        assert mcp._is_component_enabled(tool_other) is False

    def test_exclude_takes_precedence_over_include(self):
        """exclude_tags takes precedence over include_tags."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"}, exclude_tags={"deprecated"})

        tool = Tool(name="tool", parameters={}, tags={"public", "deprecated"})
        assert mcp._is_component_enabled(tool) is False
