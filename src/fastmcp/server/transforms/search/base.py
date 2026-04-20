"""Deprecated shim. See ``fastmcp.server.plugins.search.base``."""

from fastmcp.server.plugins.search.base import *  # noqa: F403
from fastmcp.server.plugins.search.base import (  # noqa: F401
    BaseSearchTransform,
    SearchResultSerializer,
    _extract_searchable_text,
    serialize_tools_for_output_json,
    serialize_tools_for_output_markdown,
)
