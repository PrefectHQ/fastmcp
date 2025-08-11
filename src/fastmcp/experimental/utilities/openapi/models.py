"""Intermediate Representation (IR) models for OpenAPI operations."""

from typing import Any, Literal

from pydantic import Field

from fastmcp.utilities.types import FastMCPBaseModel

# Type definitions
HttpMethod = Literal[
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"
]
ParameterLocation = Literal["path", "query", "header", "cookie"]
JsonSchema = dict[str, Any]


class ParameterInfo(FastMCPBaseModel):
    """Represents a single parameter for an HTTP operation in our IR."""

    name: str
    location: ParameterLocation  # Mapped from 'in' field of openapi-pydantic Parameter
    required: bool = False
    schema_: JsonSchema = Field(..., alias="schema")  # Target name in IR
    description: str | None = None
    explode: bool | None = None  # OpenAPI explode property for array parameters
    style: str | None = None  # OpenAPI style property for parameter serialization


class RequestBodyInfo(FastMCPBaseModel):
    """Represents the request body for an HTTP operation in our IR."""

    required: bool = False
    content_schema: dict[str, JsonSchema] = Field(
        default_factory=dict
    )  # Key: media type
    description: str | None = None


class ResponseInfo(FastMCPBaseModel):
    """Represents response information in our IR."""

    description: str | None = None
    # Store schema per media type, key is media type
    content_schema: dict[str, JsonSchema] = Field(default_factory=dict)


class HTTPRoute(FastMCPBaseModel):
    """Intermediate Representation for a single OpenAPI operation."""

    path: str
    method: HttpMethod
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: list[ParameterInfo] = Field(default_factory=list)
    request_body: RequestBodyInfo | None = None
    responses: dict[str, ResponseInfo] = Field(
        default_factory=dict
    )  # Key: status code str
    request_schemas: dict[str, JsonSchema] = Field(
        default_factory=dict
    )  # Store schemas needed for input (parameters/request body)
    response_schemas: dict[str, JsonSchema] = Field(
        default_factory=dict
    )  # Store schemas needed for output (responses)
    extensions: dict[str, Any] = Field(default_factory=dict)
    openapi_version: str | None = None

    # Pre-calculated fields for performance
    flat_param_schema: JsonSchema = Field(
        default_factory=dict
    )  # Combined schema for MCP tools
    parameter_map: dict[str, dict[str, str]] = Field(
        default_factory=dict
    )  # Maps flat args to locations


# Export public symbols
__all__ = [
    "HTTPRoute",
    "ParameterInfo",
    "RequestBodyInfo",
    "ResponseInfo",
    "HttpMethod",
    "ParameterLocation",
    "JsonSchema",
]
