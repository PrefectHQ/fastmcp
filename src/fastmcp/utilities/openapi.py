import json
import logging
from typing import Any, Generic, Literal, TypeVar

from openapi_pydantic import (
    OpenAPI,
    Operation,
    Parameter,
    PathItem,
    Reference,
    RequestBody,
    Response,
    Schema,
)

# Import OpenAPI 3.0 models as well
from openapi_pydantic.v3.v3_0 import OpenAPI as OpenAPI_30
from openapi_pydantic.v3.v3_0 import Operation as Operation_30
from openapi_pydantic.v3.v3_0 import Parameter as Parameter_30
from openapi_pydantic.v3.v3_0 import PathItem as PathItem_30
from openapi_pydantic.v3.v3_0 import Reference as Reference_30
from openapi_pydantic.v3.v3_0 import RequestBody as RequestBody_30
from openapi_pydantic.v3.v3_0 import Response as Response_30
from openapi_pydantic.v3.v3_0 import Schema as Schema_30
from pydantic import BaseModel, Field, ValidationError

from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import FastMCPBaseModel

logger = logging.getLogger(__name__)

# --- Intermediate Representation (IR) Definition ---
# (IR models remain the same)

HttpMethod = Literal[
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"
]
ParameterLocation = Literal["path", "query", "header", "cookie"]
JsonSchema = dict[str, Any]


def format_array_parameter(
    values: list, parameter_name: str, is_query_parameter: bool = False
) -> str | list:
    """
    Format an array parameter according to OpenAPI specifications.

    Args:
        values: List of values to format
        parameter_name: Name of the parameter (for error messages)
        is_query_parameter: If True, can return list for explode=True behavior

    Returns:
        String (comma-separated) or list (for query params with explode=True)
    """
    # For arrays of simple types (strings, numbers, etc.), join with commas
    if all(isinstance(item, str | int | float | bool) for item in values):
        return ",".join(str(v) for v in values)

    # For complex types, try to create a simpler representation
    try:
        # Try to create a simple string representation
        formatted_parts = []
        for item in values:
            if isinstance(item, dict):
                # For objects, serialize key-value pairs
                item_parts = []
                for k, v in item.items():
                    item_parts.append(f"{k}:{v}")
                formatted_parts.append(".".join(item_parts))
            else:
                formatted_parts.append(str(item))

        return ",".join(formatted_parts)
    except Exception as e:
        param_type = "query" if is_query_parameter else "path"
        logger.warning(
            f"Failed to format complex array {param_type} parameter '{parameter_name}': {e}"
        )

        if is_query_parameter:
            # For query parameters, fallback to original list
            return values
        else:
            # For path parameters, fallback to string representation without Python syntax
            str_value = (
                str(values)
                .replace("[", "")
                .replace("]", "")
                .replace("'", "")
                .replace('"', "")
            )
            return str_value


class ParameterInfo(FastMCPBaseModel):
    """Represents a single parameter for an HTTP operation in our IR."""

    name: str
    location: ParameterLocation  # Mapped from 'in' field of openapi-pydantic Parameter
    required: bool = False
    schema_: JsonSchema = Field(..., alias="schema")  # Target name in IR
    description: str | None = None
    explode: bool | None = None  # OpenAPI explode property for array parameters


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
    schema_definitions: dict[str, JsonSchema] = Field(
        default_factory=dict
    )  # Store component schemas
    extensions: dict[str, Any] = Field(default_factory=dict)


# Export public symbols
__all__ = [
    "HTTPRoute",
    "ParameterInfo",
    "RequestBodyInfo",
    "ResponseInfo",
    "HttpMethod",
    "ParameterLocation",
    "JsonSchema",
    "parse_openapi_to_http_routes",
]

# Type variables for generic parser
TOpenAPI = TypeVar("TOpenAPI", OpenAPI, OpenAPI_30)
TSchema = TypeVar("TSchema", Schema, Schema_30)
TReference = TypeVar("TReference", Reference, Reference_30)
TParameter = TypeVar("TParameter", Parameter, Parameter_30)
TRequestBody = TypeVar("TRequestBody", RequestBody, RequestBody_30)
TResponse = TypeVar("TResponse", Response, Response_30)
TOperation = TypeVar("TOperation", Operation, Operation_30)
TPathItem = TypeVar("TPathItem", PathItem, PathItem_30)


def parse_openapi_to_http_routes(openapi_dict: dict[str, Any]) -> list[HTTPRoute]:
    """
    Parses an OpenAPI schema dictionary into a list of HTTPRoute objects
    using the openapi-pydantic library.

    Supports both OpenAPI 3.0.x and 3.1.x versions.
    """
    # Check OpenAPI version to use appropriate model
    openapi_version = openapi_dict.get("openapi", "")

    try:
        if openapi_version.startswith("3.0"):
            # Use OpenAPI 3.0 models
            openapi_30 = OpenAPI_30.model_validate(openapi_dict)
            logger.info(
                f"Successfully parsed OpenAPI 3.0 schema version: {openapi_30.openapi}"
            )
            parser = OpenAPIParser(
                openapi_30,
                Reference_30,
                Schema_30,
                Parameter_30,
                RequestBody_30,
                Response_30,
                Operation_30,
                PathItem_30,
            )
            return parser.parse()
        else:
            # Default to OpenAPI 3.1 models
            openapi_31 = OpenAPI.model_validate(openapi_dict)
            logger.info(
                f"Successfully parsed OpenAPI 3.1 schema version: {openapi_31.openapi}"
            )
            parser = OpenAPIParser(
                openapi_31,
                Reference,
                Schema,
                Parameter,
                RequestBody,
                Response,
                Operation,
                PathItem,
            )
            return parser.parse()
    except ValidationError as e:
        logger.error(f"OpenAPI schema validation failed: {e}")
        error_details = e.errors()
        logger.error(f"Validation errors: {error_details}")
        raise ValueError(f"Invalid OpenAPI schema: {error_details}") from e


class OpenAPIParser(
    Generic[
        TOpenAPI,
        TReference,
        TSchema,
        TParameter,
        TRequestBody,
        TResponse,
        TOperation,
        TPathItem,
    ]
):
    """Unified parser for OpenAPI schemas with generic type parameters to handle both 3.0 and 3.1."""

    def __init__(
        self,
        openapi: TOpenAPI,
        reference_cls: type[TReference],
        schema_cls: type[TSchema],
        parameter_cls: type[TParameter],
        request_body_cls: type[TRequestBody],
        response_cls: type[TResponse],
        operation_cls: type[TOperation],
        path_item_cls: type[TPathItem],
    ):
        """Initialize the parser with the OpenAPI schema and type classes."""
        self.openapi = openapi
        self.reference_cls = reference_cls
        self.schema_cls = schema_cls
        self.parameter_cls = parameter_cls
        self.request_body_cls = request_body_cls
        self.response_cls = response_cls
        self.operation_cls = operation_cls
        self.path_item_cls = path_item_cls

    def _convert_to_parameter_location(self, param_in: str) -> ParameterLocation:
        """Convert string parameter location to our ParameterLocation type."""
        if param_in in ["path", "query", "header", "cookie"]:
            return param_in  # type: ignore[return-value]  # Safe cast since we checked values
        logger.warning(f"Unknown parameter location: {param_in}, defaulting to 'query'")
        return "query"  # type: ignore[return-value]  # Safe cast to default value

    def _resolve_ref(self, item: Any) -> Any:
        """Resolves a reference to its target definition."""
        if isinstance(item, self.reference_cls):
            ref_str = item.ref
            try:
                if not ref_str.startswith("#/"):
                    raise ValueError(
                        f"External or non-local reference not supported: {ref_str}"
                    )

                parts = ref_str.strip("#/").split("/")
                target = self.openapi

                for part in parts:
                    if part.isdigit() and isinstance(target, list):
                        target = target[int(part)]
                    elif isinstance(target, BaseModel):
                        # Check class fields first, then model_extra
                        if part in target.__class__.model_fields:
                            target = getattr(target, part, None)
                        elif target.model_extra and part in target.model_extra:
                            target = target.model_extra[part]
                        else:
                            # Special handling for components
                            if part == "components" and hasattr(target, "components"):
                                target = getattr(target, "components")
                            elif hasattr(target, part):  # Fallback check
                                target = getattr(target, part, None)
                            else:
                                target = None  # Part not found
                    elif isinstance(target, dict):
                        target = target.get(part)
                    else:
                        raise ValueError(
                            f"Cannot traverse part '{part}' in reference '{ref_str}'"
                        )

                    if target is None:
                        raise ValueError(
                            f"Reference part '{part}' not found in path '{ref_str}'"
                        )

                # Handle nested references
                if isinstance(target, self.reference_cls):
                    return self._resolve_ref(target)

                return target
            except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
                raise ValueError(f"Failed to resolve reference '{ref_str}': {e}") from e

        return item

    def _extract_schema_as_dict(self, schema_obj: Any) -> JsonSchema:
        """Resolves a schema and returns it as a dictionary."""
        try:
            resolved_schema = self._resolve_ref(schema_obj)

            if isinstance(resolved_schema, (self.schema_cls)):
                # Convert schema to dictionary
                result = resolved_schema.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            elif isinstance(resolved_schema, dict):
                result = resolved_schema
            else:
                logger.warning(
                    f"Expected Schema after resolving, got {type(resolved_schema)}. Returning empty dict."
                )
                result = {}

            return _replace_ref_with_defs(result)
        except ValueError as e:
            # Re-raise ValueError for external reference errors and other validation issues
            if "External or non-local reference not supported" in str(e):
                raise
            logger.error(f"Failed to extract schema as dict: {e}", exc_info=False)
            return {}
        except Exception as e:
            logger.error(f"Failed to extract schema as dict: {e}", exc_info=False)
            return {}

    def _extract_parameters(
        self,
        operation_params: list[Any] | None = None,
        path_item_params: list[Any] | None = None,
    ) -> list[ParameterInfo]:
        """Extract and resolve parameters from operation and path item."""
        extracted_params: list[ParameterInfo] = []
        seen_params: dict[
            tuple[str, str], bool
        ] = {}  # Use tuple of (name, location) as key
        all_params = (operation_params or []) + (path_item_params or [])

        for param_or_ref in all_params:
            try:
                parameter = self._resolve_ref(param_or_ref)

                if not isinstance(parameter, self.parameter_cls):
                    logger.warning(
                        f"Expected Parameter after resolving, got {type(parameter)}. Skipping."
                    )
                    continue

                # Extract parameter info - handle both 3.0 and 3.1 parameter models
                param_in = parameter.param_in  # Both use param_in
                # Handle enum or string parameter locations
                from enum import Enum

                param_in_str = (
                    param_in.value if isinstance(param_in, Enum) else param_in
                )
                param_location = self._convert_to_parameter_location(param_in_str)
                param_schema_obj = parameter.param_schema  # Both use param_schema

                # Skip duplicate parameters (same name and location)
                param_key = (parameter.name, param_in_str)
                if param_key in seen_params:
                    continue
                seen_params[param_key] = True

                # Extract schema
                param_schema_dict = {}
                if param_schema_obj:
                    # Process schema object
                    param_schema_dict = self._extract_schema_as_dict(param_schema_obj)

                    # Handle default value
                    resolved_schema = self._resolve_ref(param_schema_obj)
                    if (
                        not isinstance(resolved_schema, self.reference_cls)
                        and hasattr(resolved_schema, "default")
                        and resolved_schema.default is not None
                    ):
                        param_schema_dict["default"] = resolved_schema.default

                elif hasattr(parameter, "content") and parameter.content:
                    # Handle content-based parameters
                    first_media_type = next(iter(parameter.content.values()), None)
                    if (
                        first_media_type
                        and hasattr(first_media_type, "media_type_schema")
                        and first_media_type.media_type_schema
                    ):
                        media_schema = first_media_type.media_type_schema
                        param_schema_dict = self._extract_schema_as_dict(media_schema)

                        # Handle default value in content schema
                        resolved_media_schema = self._resolve_ref(media_schema)
                        if (
                            not isinstance(resolved_media_schema, self.reference_cls)
                            and hasattr(resolved_media_schema, "default")
                            and resolved_media_schema.default is not None
                        ):
                            param_schema_dict["default"] = resolved_media_schema.default

                # Extract explode property if present
                explode = getattr(parameter, "explode", None)

                # Create parameter info object
                param_info = ParameterInfo(
                    name=parameter.name,
                    location=param_location,
                    required=parameter.required,
                    schema=param_schema_dict,
                    description=parameter.description,
                    explode=explode,
                )
                extracted_params.append(param_info)
            except Exception as e:
                param_name = getattr(
                    param_or_ref, "name", getattr(param_or_ref, "ref", "unknown")
                )
                logger.error(
                    f"Failed to extract parameter '{param_name}': {e}", exc_info=False
                )

        return extracted_params

    def _extract_request_body(self, request_body_or_ref: Any) -> RequestBodyInfo | None:
        """Extract and resolve request body information."""
        if not request_body_or_ref:
            return None

        try:
            request_body = self._resolve_ref(request_body_or_ref)

            if not isinstance(request_body, self.request_body_cls):
                logger.warning(
                    f"Expected RequestBody after resolving, got {type(request_body)}. Returning None."
                )
                return None

            # Create request body info
            request_body_info = RequestBodyInfo(
                required=request_body.required,
                description=request_body.description,
            )

            # Extract content schemas
            if hasattr(request_body, "content") and request_body.content:
                for media_type_str, media_type_obj in request_body.content.items():
                    if (
                        media_type_obj
                        and hasattr(media_type_obj, "media_type_schema")
                        and media_type_obj.media_type_schema
                    ):
                        try:
                            schema_dict = self._extract_schema_as_dict(
                                media_type_obj.media_type_schema
                            )
                            request_body_info.content_schema[media_type_str] = (
                                schema_dict
                            )
                        except ValueError as e:
                            # Re-raise ValueError for external reference errors
                            if "External or non-local reference not supported" in str(
                                e
                            ):
                                raise
                            logger.error(
                                f"Failed to extract schema for media type '{media_type_str}': {e}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to extract schema for media type '{media_type_str}': {e}"
                            )

            return request_body_info
        except ValueError as e:
            # Re-raise ValueError for external reference errors
            if "External or non-local reference not supported" in str(e):
                raise
            ref_name = getattr(request_body_or_ref, "ref", "unknown")
            logger.error(
                f"Failed to extract request body '{ref_name}': {e}", exc_info=False
            )
            return None
        except Exception as e:
            ref_name = getattr(request_body_or_ref, "ref", "unknown")
            logger.error(
                f"Failed to extract request body '{ref_name}': {e}", exc_info=False
            )
            return None

    def _extract_responses(
        self, operation_responses: dict[str, Any] | None
    ) -> dict[str, ResponseInfo]:
        """Extract and resolve response information."""
        extracted_responses: dict[str, ResponseInfo] = {}

        if not operation_responses:
            return extracted_responses

        for status_code, resp_or_ref in operation_responses.items():
            try:
                response = self._resolve_ref(resp_or_ref)

                if not isinstance(response, self.response_cls):
                    logger.warning(
                        f"Expected Response after resolving for status code {status_code}, "
                        f"got {type(response)}. Skipping."
                    )
                    continue

                # Create response info
                resp_info = ResponseInfo(description=response.description)

                # Extract content schemas
                if hasattr(response, "content") and response.content:
                    for media_type_str, media_type_obj in response.content.items():
                        if (
                            media_type_obj
                            and hasattr(media_type_obj, "media_type_schema")
                            and media_type_obj.media_type_schema
                        ):
                            try:
                                schema_dict = self._extract_schema_as_dict(
                                    media_type_obj.media_type_schema
                                )
                                resp_info.content_schema[media_type_str] = schema_dict
                            except ValueError as e:
                                # Re-raise ValueError for external reference errors
                                if (
                                    "External or non-local reference not supported"
                                    in str(e)
                                ):
                                    raise
                                logger.error(
                                    f"Failed to extract schema for media type '{media_type_str}' "
                                    f"in response {status_code}: {e}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to extract schema for media type '{media_type_str}' "
                                    f"in response {status_code}: {e}"
                                )

                extracted_responses[str(status_code)] = resp_info
            except ValueError as e:
                # Re-raise ValueError for external reference errors
                if "External or non-local reference not supported" in str(e):
                    raise
                ref_name = getattr(resp_or_ref, "ref", "unknown")
                logger.error(
                    f"Failed to extract response for status code {status_code} "
                    f"from reference '{ref_name}': {e}",
                    exc_info=False,
                )
            except Exception as e:
                ref_name = getattr(resp_or_ref, "ref", "unknown")
                logger.error(
                    f"Failed to extract response for status code {status_code} "
                    f"from reference '{ref_name}': {e}",
                    exc_info=False,
                )

        return extracted_responses

    def parse(self) -> list[HTTPRoute]:
        """Parse the OpenAPI schema into HTTP routes."""
        routes: list[HTTPRoute] = []

        if not hasattr(self.openapi, "paths") or not self.openapi.paths:
            logger.warning("OpenAPI schema has no paths defined.")
            return []

        # Extract component schemas
        schema_definitions = {}
        if hasattr(self.openapi, "components") and self.openapi.components:
            components = self.openapi.components
            if hasattr(components, "schemas") and components.schemas:
                for name, schema in components.schemas.items():
                    try:
                        if isinstance(schema, self.reference_cls):
                            resolved_schema = self._resolve_ref(schema)
                            schema_definitions[name] = self._extract_schema_as_dict(
                                resolved_schema
                            )
                        else:
                            schema_definitions[name] = self._extract_schema_as_dict(
                                schema
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to extract schema definition '{name}': {e}"
                        )

        # Process paths and operations
        for path_str, path_item_obj in self.openapi.paths.items():
            if not isinstance(path_item_obj, self.path_item_cls):
                logger.warning(
                    f"Skipping invalid path item for path '{path_str}' (type: {type(path_item_obj)})"
                )
                continue

            path_level_params = (
                path_item_obj.parameters
                if hasattr(path_item_obj, "parameters")
                else None
            )

            # Get HTTP methods from the path item class fields
            http_methods = [
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            ]
            for method_lower in http_methods:
                operation = getattr(path_item_obj, method_lower, None)

                if operation and isinstance(operation, self.operation_cls):
                    # Cast method to HttpMethod - safe since we only use valid HTTP methods
                    method_upper = method_lower.upper()

                    try:
                        parameters = self._extract_parameters(
                            getattr(operation, "parameters", None), path_level_params
                        )

                        request_body_info = self._extract_request_body(
                            getattr(operation, "requestBody", None)
                        )

                        responses = self._extract_responses(
                            getattr(operation, "responses", None)
                        )

                        extensions = {}
                        if hasattr(operation, "model_extra") and operation.model_extra:
                            extensions = {
                                k: v
                                for k, v in operation.model_extra.items()
                                if k.startswith("x-")
                            }

                        route = HTTPRoute(
                            path=path_str,
                            method=method_upper,  # type: ignore[arg-type]  # Known valid HTTP method
                            operation_id=getattr(operation, "operationId", None),
                            summary=getattr(operation, "summary", None),
                            description=getattr(operation, "description", None),
                            tags=getattr(operation, "tags", []) or [],
                            parameters=parameters,
                            request_body=request_body_info,
                            responses=responses,
                            schema_definitions=schema_definitions,
                            extensions=extensions,
                        )
                        routes.append(route)
                        logger.info(
                            f"Successfully extracted route: {method_upper} {path_str}"
                        )
                    except ValueError as op_error:
                        # Re-raise ValueError for external reference errors
                        if "External or non-local reference not supported" in str(
                            op_error
                        ):
                            raise
                        op_id = getattr(operation, "operationId", "unknown")
                        logger.error(
                            f"Failed to process operation {method_upper} {path_str} (ID: {op_id}): {op_error}",
                            exc_info=True,
                        )
                    except Exception as op_error:
                        op_id = getattr(operation, "operationId", "unknown")
                        logger.error(
                            f"Failed to process operation {method_upper} {path_str} (ID: {op_id}): {op_error}",
                            exc_info=True,
                        )

        logger.info(f"Finished parsing. Extracted {len(routes)} HTTP routes.")
        return routes


def clean_schema_for_display(schema: JsonSchema | None) -> JsonSchema | None:
    """
    Clean up a schema dictionary for display by removing internal/complex fields.
    """
    if not schema or not isinstance(schema, dict):
        return schema

    # Make a copy to avoid modifying the input schema
    cleaned = schema.copy()

    # Fields commonly removed for simpler display to LLMs or users
    fields_to_remove = [
        "allOf",
        "anyOf",
        "oneOf",
        "not",  # Composition keywords
        "nullable",  # Handled by type unions usually
        "discriminator",
        "readOnly",
        "writeOnly",
        "deprecated",
        "xml",
        "externalDocs",
        # Can be verbose, maybe remove based on flag?
        # "pattern", "minLength", "maxLength",
        # "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        # "multipleOf", "minItems", "maxItems", "uniqueItems",
        # "minProperties", "maxProperties"
    ]

    for field in fields_to_remove:
        if field in cleaned:
            cleaned.pop(field)

    # Recursively clean properties and items
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: clean_schema_for_display(v) for k, v in cleaned["properties"].items()
        }
        # Remove properties section if empty after cleaning
        if not cleaned["properties"]:
            cleaned.pop("properties")

    if "items" in cleaned:
        cleaned["items"] = clean_schema_for_display(cleaned["items"])
        # Remove items section if empty after cleaning
        if not cleaned["items"]:
            cleaned.pop("items")

    if "additionalProperties" in cleaned:
        # Often verbose, can be simplified
        if isinstance(cleaned["additionalProperties"], dict):
            cleaned["additionalProperties"] = clean_schema_for_display(
                cleaned["additionalProperties"]
            )
        elif cleaned["additionalProperties"] is True:
            # Maybe keep 'true' or represent as 'Allows additional properties' text?
            pass  # Keep simple boolean for now


def generate_example_from_schema(schema: JsonSchema | None) -> Any:
    """
    Generate a simple example value from a JSON schema dictionary.
    Very basic implementation focusing on types.
    """
    if not schema or not isinstance(schema, dict):
        return "unknown"  # Or None?

    # Use default value if provided
    if "default" in schema:
        return schema["default"]
    # Use first enum value if provided
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]
    # Use first example if provided
    if (
        "examples" in schema
        and isinstance(schema["examples"], list)
        and schema["examples"]
    ):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]

    schema_type = schema.get("type")

    if schema_type == "object":
        result = {}
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            # Generate example for first few properties or required ones? Limit complexity.
            required_props = set(schema.get("required", []))
            props_to_include = list(properties.keys())[
                :3
            ]  # Limit to first 3 for brevity
            for prop_name in props_to_include:
                if prop_name in properties:
                    result[prop_name] = generate_example_from_schema(
                        properties[prop_name]
                    )
            # Ensure required props are present if possible
            for req_prop in required_props:
                if req_prop not in result and req_prop in properties:
                    result[req_prop] = generate_example_from_schema(
                        properties[req_prop]
                    )
        return result if result else {"key": "value"}  # Basic object if no props

    elif schema_type == "array":
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            # Generate one example item
            item_example = generate_example_from_schema(items_schema)
            return [item_example] if item_example is not None else []
        return ["example_item"]  # Fallback

    elif schema_type == "string":
        format_type = schema.get("format")
        if format_type == "date-time":
            return "2024-01-01T12:00:00Z"
        if format_type == "date":
            return "2024-01-01"
        if format_type == "email":
            return "user@example.com"
        if format_type == "uuid":
            return "123e4567-e89b-12d3-a456-426614174000"
        if format_type == "byte":
            return "ZXhhbXBsZQ=="  # "example" base64
        return "string"

    elif schema_type == "integer":
        return 1
    elif schema_type == "number":
        return 1.5
    elif schema_type == "boolean":
        return True
    elif schema_type == "null":
        return None

    # Fallback if type is unknown or missing
    return "unknown_type"


def format_json_for_description(data: Any, indent: int = 2) -> str:
    """Formats Python data as a JSON string block for markdown."""
    try:
        json_str = json.dumps(data, indent=indent)
        return f"```json\n{json_str}\n```"
    except TypeError:
        return f"```\nCould not serialize to JSON: {data}\n```"


def format_description_with_responses(
    base_description: str,
    responses: dict[
        str, Any
    ],  # Changed from specific ResponseInfo type to avoid circular imports
    parameters: list[ParameterInfo] | None = None,  # Add parameters parameter
    request_body: RequestBodyInfo | None = None,  # Add request_body parameter
) -> str:
    """
    Formats the base description string with response, parameter, and request body information.

    Args:
        base_description (str): The initial description to be formatted.
        responses (dict[str, Any]): A dictionary of response information, keyed by status code.
        parameters (list[ParameterInfo] | None, optional): A list of parameter information,
            including path and query parameters. Each parameter includes details such as name,
            location, whether it is required, and a description.
        request_body (RequestBodyInfo | None, optional): Information about the request body,
            including its description, whether it is required, and its content schema.

    Returns:
        str: The formatted description string with additional details about responses, parameters,
        and the request body.
    """
    desc_parts = [base_description]

    # Add parameter information
    if parameters:
        # Process path parameters
        path_params = [p for p in parameters if p.location == "path"]
        if path_params:
            param_section = "\n\n**Path Parameters:**"
            desc_parts.append(param_section)
            for param in path_params:
                required_marker = " (Required)" if param.required else ""
                param_desc = f"\n- **{param.name}**{required_marker}: {param.description or 'No description.'}"
                desc_parts.append(param_desc)

        # Process query parameters
        query_params = [p for p in parameters if p.location == "query"]
        if query_params:
            param_section = "\n\n**Query Parameters:**"
            desc_parts.append(param_section)
            for param in query_params:
                required_marker = " (Required)" if param.required else ""
                param_desc = f"\n- **{param.name}**{required_marker}: {param.description or 'No description.'}"
                desc_parts.append(param_desc)

    # Add request body information if present
    if request_body and request_body.description:
        req_body_section = "\n\n**Request Body:**"
        desc_parts.append(req_body_section)
        required_marker = " (Required)" if request_body.required else ""
        desc_parts.append(f"\n{request_body.description}{required_marker}")

        # Add request body property descriptions if available
        if request_body.content_schema:
            media_type = (
                "application/json"
                if "application/json" in request_body.content_schema
                else next(iter(request_body.content_schema), None)
            )
            if media_type:
                schema = request_body.content_schema.get(media_type, {})
                if isinstance(schema, dict) and "properties" in schema:
                    desc_parts.append("\n\n**Request Properties:**")
                    for prop_name, prop_schema in schema["properties"].items():
                        if (
                            isinstance(prop_schema, dict)
                            and "description" in prop_schema
                        ):
                            required = prop_name in schema.get("required", [])
                            req_mark = " (Required)" if required else ""
                            desc_parts.append(
                                f"\n- **{prop_name}**{req_mark}: {prop_schema['description']}"
                            )

    # Add response information
    if responses:
        response_section = "\n\n**Responses:**"
        added_response_section = False

        # Determine success codes (common ones)
        success_codes = {"200", "201", "202", "204"}  # As strings
        success_status = next((s for s in success_codes if s in responses), None)

        # Process all responses
        responses_to_process = responses.items()

        for status_code, resp_info in sorted(responses_to_process):
            if not added_response_section:
                desc_parts.append(response_section)
                added_response_section = True

            status_marker = " (Success)" if status_code == success_status else ""
            desc_parts.append(
                f"\n- **{status_code}**{status_marker}: {resp_info.description or 'No description.'}"
            )

            # Process content schemas for this response
            if resp_info.content_schema:
                # Prioritize json, then take first available
                media_type = (
                    "application/json"
                    if "application/json" in resp_info.content_schema
                    else next(iter(resp_info.content_schema), None)
                )

                if media_type:
                    schema = resp_info.content_schema.get(media_type)
                    desc_parts.append(f"  - Content-Type: `{media_type}`")

                    # Add response property descriptions
                    if isinstance(schema, dict):
                        # Handle array responses
                        if schema.get("type") == "array" and "items" in schema:
                            items_schema = schema["items"]
                            if (
                                isinstance(items_schema, dict)
                                and "properties" in items_schema
                            ):
                                desc_parts.append("\n  - **Response Item Properties:**")
                                for prop_name, prop_schema in items_schema[
                                    "properties"
                                ].items():
                                    if (
                                        isinstance(prop_schema, dict)
                                        and "description" in prop_schema
                                    ):
                                        desc_parts.append(
                                            f"\n    - **{prop_name}**: {prop_schema['description']}"
                                        )
                        # Handle object responses
                        elif "properties" in schema:
                            desc_parts.append("\n  - **Response Properties:**")
                            for prop_name, prop_schema in schema["properties"].items():
                                if (
                                    isinstance(prop_schema, dict)
                                    and "description" in prop_schema
                                ):
                                    desc_parts.append(
                                        f"\n    - **{prop_name}**: {prop_schema['description']}"
                                    )

                    # Generate Example
                    if schema:
                        example = generate_example_from_schema(schema)
                        if example != "unknown_type" and example is not None:
                            desc_parts.append("\n  - **Example:**")
                            desc_parts.append(
                                format_json_for_description(example, indent=2)
                            )

    return "\n".join(desc_parts)


def _replace_ref_with_defs(
    info: dict[str, Any], description: str | None = None
) -> dict[str, Any]:
    """
    Replace openapi $ref with jsonschema $defs

    Examples:
    - {"type": "object", "properties": {"$ref": "#/components/schemas/..."}}
    - {"$ref": "#/components/schemas/..."}
    - {"items": {"$ref": "#/components/schemas/..."}}
    - {"anyOf": [{"$ref": "#/components/schemas/..."}]}
    - {"allOf": [{"$ref": "#/components/schemas/..."}]}
    - {"oneOf": [{"$ref": "#/components/schemas/..."}]}

    Args:
        info: dict[str, Any]
        description: str | None

    Returns:
        dict[str, Any]
    """
    schema = info.copy()
    if ref_path := schema.get("$ref"):
        if ref_path.startswith("#/components/schemas/"):
            schema_name = ref_path.split("/")[-1]
            schema["$ref"] = f"#/$defs/{schema_name}"
        elif not ref_path.startswith("#/"):
            raise ValueError(
                f"External or non-local reference not supported: {ref_path}. "
                f"FastMCP only supports local schema references starting with '#/'. "
                f"Please include all schema definitions within the OpenAPI document."
            )
    elif properties := schema.get("properties"):
        if "$ref" in properties:
            schema["properties"] = _replace_ref_with_defs(properties)
        else:
            schema["properties"] = {
                prop_name: _replace_ref_with_defs(prop_schema)
                for prop_name, prop_schema in properties.items()
            }
    elif item_schema := schema.get("items"):
        schema["items"] = _replace_ref_with_defs(item_schema)
    for section in ["anyOf", "allOf", "oneOf"]:
        for i, item in enumerate(schema.get(section, [])):
            schema[section][i] = _replace_ref_with_defs(item)
    if info.get("description", description) and not schema.get("description"):
        schema["description"] = description
    return schema


def _combine_schemas(route: HTTPRoute) -> dict[str, Any]:
    """
    Combines parameter and request body schemas into a single schema.

    Args:
        route: HTTPRoute object

    Returns:
        Combined schema dictionary
    """
    properties = {}
    required = []

    # Add path parameters
    for param in route.parameters:
        if param.required:
            required.append(param.name)
        properties[param.name] = _replace_ref_with_defs(
            param.schema_.copy(), param.description
        )

    # Add request body if it exists
    if route.request_body and route.request_body.content_schema:
        # For now, just use the first content type's schema
        content_type = next(iter(route.request_body.content_schema))
        body_schema = _replace_ref_with_defs(
            route.request_body.content_schema[content_type].copy(),
            route.request_body.description,
        )
        body_props = body_schema.get("properties", {})

        # Add request body properties
        for prop_name, prop_schema in body_props.items():
            properties[prop_name] = prop_schema

        if route.request_body.required:
            required.extend(body_schema.get("required", []))

    result = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    # Add schema definitions if available
    if route.schema_definitions:
        result["$defs"] = route.schema_definitions

    # Use compress_schema to remove unused definitions
    result = compress_schema(result)

    return result
