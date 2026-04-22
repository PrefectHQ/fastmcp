"""Deprecation shim — OpenAPI component classes moved to
`fastmcp.server.plugins.openapi.components`.
"""

import warnings

from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.plugins.openapi.components import (
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
    _extract_mime_type_from_route,
    _redact_headers,
    _slugify,
)

warnings.warn(
    "fastmcp.server.providers.openapi.components has moved to "
    "fastmcp.server.plugins.openapi.components. Prefer the OpenAPI "
    "plugin: `from fastmcp.server.plugins.openapi import OpenAPI`. This "
    "old leaf-submodule import path will be removed in a future release.",
    FastMCPDeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "OpenAPIResource",
    "OpenAPIResourceTemplate",
    "OpenAPITool",
    "_extract_mime_type_from_route",
    "_redact_headers",
    "_slugify",
]
