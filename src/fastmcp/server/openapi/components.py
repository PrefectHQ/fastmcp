"""OpenAPI component implementations - backwards compatibility stub.

This module is deprecated. Import from fastmcp.server.plugins.openapi instead.
"""

from __future__ import annotations

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning

warnings.warn(
    "fastmcp.server.openapi.components is deprecated. "
    "Import from fastmcp.server.plugins.openapi instead.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

from fastmcp.server.plugins.openapi.components import (  # noqa: E402
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)

# Export public symbols
__all__ = [
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
]
