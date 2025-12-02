"""Deprecated: Import from fastmcp.utilities.openapi instead."""

import warnings

from fastmcp.utilities.openapi import (
    HTTPRoute,
    HttpMethod,
    ParameterInfo,
    ParameterLocation,
    RequestBodyInfo,
    ResponseInfo,
    extract_output_schema_from_responses,
    format_simple_description,
    parse_openapi_to_http_routes,
    _combine_schemas,
)

# Deprecated in 2.14 when OpenAPI support was promoted out of experimental
warnings.warn(
    "Importing from fastmcp.experimental.utilities.openapi is deprecated. "
    "Import from fastmcp.utilities.openapi instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "HTTPRoute",
    "HttpMethod",
    "ParameterInfo",
    "ParameterLocation",
    "RequestBodyInfo",
    "ResponseInfo",
    "_combine_schemas",
    "extract_output_schema_from_responses",
    "format_simple_description",
    "parse_openapi_to_http_routes",
]
