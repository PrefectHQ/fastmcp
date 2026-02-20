"""Tests for removed include_tags/exclude_tags parameters."""

import pytest

from fastmcp import FastMCP


class TestIncludeExcludeTagsRemoved:
    """Test that include_tags/exclude_tags raise TypeError with migration hints."""

    def test_exclude_tags_raises_type_error(self):
        with pytest.raises(TypeError, match="no longer accepts `exclude_tags`"):
            FastMCP(exclude_tags={"internal"})

    def test_include_tags_raises_type_error(self):
        with pytest.raises(TypeError, match="no longer accepts `include_tags`"):
            FastMCP(include_tags={"public"})

    def test_exclude_tags_error_mentions_disable(self):
        with pytest.raises(TypeError, match="server.disable"):
            FastMCP(exclude_tags={"internal"})

    def test_include_tags_error_mentions_enable(self):
        with pytest.raises(TypeError, match="server.enable"):
            FastMCP(include_tags={"public"})
