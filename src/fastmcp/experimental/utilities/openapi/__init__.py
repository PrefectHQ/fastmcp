"""OpenAPI utilities for FastMCP - refactored for better maintainability."""

# Import from models
from .models import (
    HTTPRoute,
    HttpMethod,
    JsonSchema,
    ParameterInfo,
    ParameterLocation,
    RequestBodyInfo,
    ResponseInfo,
)

# Import from parser
from .parser import parse_openapi_to_http_routes

# Import from formatters
from .formatters import (
    format_array_parameter,
    format_deep_object_parameter,
    format_description_with_responses,
    format_json_for_description,
    format_simple_description,
    generate_example_from_schema,
)

# Import from schemas
from .schemas import (
    _combine_schemas,
    extract_output_schema_from_responses,
    clean_schema_for_display,
    _make_optional_parameter_nullable,
)

# Import from json_schema_converter
from .json_schema_converter import (
    convert_openapi_schema_to_json_schema,
    convert_schema_definitions,
)

# Export public symbols - maintaining backward compatibility
__all__ = [
    # Models
    "HTTPRoute",
    "ParameterInfo",
    "RequestBodyInfo",
    "ResponseInfo",
    "HttpMethod",
    "ParameterLocation",
    "JsonSchema",
    # Parser
    "parse_openapi_to_http_routes",
    # Formatters
    "format_array_parameter",
    "format_deep_object_parameter",
    "format_description_with_responses",
    "format_json_for_description",
    "format_simple_description",
    "generate_example_from_schema",
    # Schemas
    "_combine_schemas",
    "extract_output_schema_from_responses",
    "clean_schema_for_display",
    "_make_optional_parameter_nullable",
    # JSON Schema Converter
    "convert_openapi_schema_to_json_schema",
    "convert_schema_definitions",
]
