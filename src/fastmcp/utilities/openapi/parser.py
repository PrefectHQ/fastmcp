"""OpenAPI parsing logic for converting OpenAPI specs to HTTPRoute objects."""

from typing import Any, Generic, TypeVar, cast

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
from pydantic import BaseModel, ValidationError

from fastmcp.utilities.logging import get_logger

from .models import (
    HTTPRoute,
    JsonSchema,
    ParameterInfo,
    ParameterLocation,
    RequestBodyInfo,
    ResponseInfo,
)
from .schemas import (
    _combine_schemas_and_map_params,
    _replace_ref_with_defs,
)

logger = get_logger(__name__)

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
            logger.debug(
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
                openapi_version,
            )
            return parser.parse()
        else:
            # Default to OpenAPI 3.1 models
            openapi_31 = OpenAPI.model_validate(openapi_dict)
            logger.debug(
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
                openapi_version,
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
        openapi_version: str,
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
        self.openapi_version = openapi_version

    def _convert_to_parameter_location(self, param_in: str) -> ParameterLocation:
        """Convert string parameter location to our ParameterLocation type."""
        if param_in in ["path", "query", "header", "cookie"]:
            return cast(ParameterLocation, param_in)
        logger.warning(f"Unknown parameter location: {param_in}, defaulting to 'query'")
        return cast(ParameterLocation, "query")

    def _resolve_ref(self, item: Any) -> Any:
        """Resolves a reference to its target definition."""
        if isinstance(item, self.reference_cls):
            ref_str = item.ref
            # Ensure ref_str is a string before calling startswith()
            if not isinstance(ref_str, str):
                return item
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
                                target = target.components
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

            if isinstance(resolved_schema, self.schema_cls):
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

            # Convert refs from OpenAPI format to JSON Schema format using recursive approach

            result = _replace_ref_with_defs(result)
            return result
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

                # Extract explode and style properties if present
                explode = getattr(parameter, "explode", None)
                style = getattr(parameter, "style", None)

                # Create parameter info object
                param_info = ParameterInfo(
                    name=parameter.name,
                    location=param_location,
                    required=parameter.required,
                    schema=param_schema_dict,
                    description=parameter.description,
                    explode=explode,
                    style=style,
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

    def _is_success_status_code(self, status_code: str) -> bool:
        """Check if a status code represents a successful response (2xx)."""
        try:
            code_int = int(status_code)
            return 200 <= code_int < 300
        except (ValueError, TypeError):
            # Handle special cases like 'default' or other non-numeric codes
            return status_code.lower() in ["default", "2xx"]

    def _get_primary_success_response(
        self, operation_responses: dict[str, Any]
    ) -> tuple[str, Any] | None:
        """Get the primary success response for an MCP tool. We only need one success response."""
        if not operation_responses:
            return None

        # Priority order: 200, 201, 202, 204, 207, then any other 2xx
        priority_codes = ["200", "201", "202", "204", "207"]

        # First check priority codes
        for code in priority_codes:
            if code in operation_responses:
                return (code, operation_responses[code])

        # Then check any other 2xx codes
        for status_code, resp_or_ref in operation_responses.items():
            if self._is_success_status_code(status_code):
                return (status_code, resp_or_ref)

        # If no success codes found, return None (tool will have no output schema)
        return None

    def _extract_responses(
        self, operation_responses: dict[str, Any] | None
    ) -> dict[str, ResponseInfo]:
        """Extract and resolve response information. Only includes the primary success response for MCP tools."""
        extracted_responses: dict[str, ResponseInfo] = {}

        if not operation_responses:
            return extracted_responses

        # For MCP tools, we only need the primary success response
        primary_response = self._get_primary_success_response(operation_responses)
        if not primary_response:
            logger.debug("No success responses found, tool will have no output schema")
            return extracted_responses

        status_code, resp_or_ref = primary_response
        logger.debug(f"Using primary success response: {status_code}")

        try:
            response = self._resolve_ref(resp_or_ref)

            if not isinstance(response, self.response_cls):
                logger.warning(
                    f"Expected Response after resolving for status code {status_code}, "
                    f"got {type(response)}. Returning empty responses."
                )
                return extracted_responses

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
                            # Track if this is a top-level $ref before resolution
                            top_level_schema_name = None
                            media_schema = media_type_obj.media_type_schema
                            if isinstance(media_schema, self.reference_cls):
                                ref_str = media_schema.ref
                                if isinstance(ref_str, str) and ref_str.startswith(
                                    "#/components/schemas/"
                                ):
                                    top_level_schema_name = ref_str.split("/")[-1]

                            schema_dict = self._extract_schema_as_dict(media_schema)
                            # Add marker for top-level schema if it was a ref
                            if top_level_schema_name:
                                schema_dict["x-fastmcp-top-level-schema"] = (
                                    top_level_schema_name
                                )
                            resp_info.content_schema[media_type_str] = schema_dict
                        except ValueError as e:
                            # Re-raise ValueError for external reference errors
                            if "External or non-local reference not supported" in str(
                                e
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

    def _extract_schema_dependencies(
        self,
        schema: dict,
        all_schemas: dict[str, Any],
        collected: set[str] | None = None,
    ) -> set[str]:
        """
        Extract all schema names referenced by a schema (including transitive dependencies).

        Args:
            schema: The schema to analyze
            all_schemas: All available schema definitions
            collected: Set of already collected schema names (for recursion)

        Returns:
            Set of schema names that are referenced
        """
        if collected is None:
            collected = set()

        def find_refs(obj):
            """Recursively find all $ref references."""
            if isinstance(obj, dict):
                if "$ref" in obj and isinstance(obj["$ref"], str):
                    ref = obj["$ref"]
                    # Handle both converted and unconverted refs
                    if ref.startswith(("#/$defs/", "#/components/schemas/")):
                        schema_name = ref.split("/")[-1]
                    else:
                        return

                    # Add this schema and recursively find its dependencies
                    if (
                        collected is not None
                        and schema_name not in collected
                        and schema_name in all_schemas
                    ):
                        collected.add(schema_name)
                        # Recursively find dependencies of this schema
                        find_refs(all_schemas[schema_name])

                # Continue searching in all values
                for value in obj.values():
                    find_refs(value)
            elif isinstance(obj, list):
                for item in obj:
                    find_refs(item)

        find_refs(schema)
        return collected

    def _extract_input_schema_dependencies(
        self,
        parameters: list[ParameterInfo],
        request_body: RequestBodyInfo | None,
        all_schemas: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Extract only the schema definitions needed for input (parameters and request body).

        Args:
            parameters: Route parameters
            request_body: Route request body
            all_schemas: All available schema definitions

        Returns:
            Dictionary containing only the schemas needed for input
        """
        needed_schemas = set()

        # Check parameters for schema references
        for param in parameters:
            if param.schema_:
                deps = self._extract_schema_dependencies(param.schema_, all_schemas)
                needed_schemas.update(deps)

        # Check request body for schema references
        if request_body and request_body.content_schema:
            for content_schema in request_body.content_schema.values():
                deps = self._extract_schema_dependencies(content_schema, all_schemas)
                needed_schemas.update(deps)

        # Return only the needed input schemas
        return {
            name: all_schemas[name] for name in needed_schemas if name in all_schemas
        }

    def _extract_output_schema_dependencies(
        self,
        responses: dict[str, ResponseInfo],
        all_schemas: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Extract only the schema definitions needed for outputs (responses).

        Args:
            responses: Route responses
            all_schemas: All available schema definitions

        Returns:
            Dictionary containing only the schemas needed for outputs
        """
        if not responses or not all_schemas:
            return {}

        needed_schemas: set[str] = set()

        for response in responses.values():
            if not response.content_schema:
                continue

            for content_schema in response.content_schema.values():
                deps = self._extract_schema_dependencies(content_schema, all_schemas)
                needed_schemas.update(deps)

                schema_name = content_schema.get("x-fastmcp-top-level-schema")
                if isinstance(schema_name, str) and schema_name in all_schemas:
                    needed_schemas.add(schema_name)
                    self._extract_schema_dependencies(
                        all_schemas[schema_name],
                        all_schemas,
                        collected=needed_schemas,
                    )

        return {
            name: all_schemas[name] for name in needed_schemas if name in all_schemas
        }

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

        # Convert schema definitions refs from OpenAPI to JSON Schema format (once)
        if schema_definitions:
            # Convert each schema definition recursively
            for name, schema in schema_definitions.items():
                if isinstance(schema, dict):
                    schema_definitions[name] = _replace_ref_with_defs(schema)

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

                        # Extract schemas separately for input and output
                        input_schemas = self._extract_input_schema_dependencies(
                            parameters,
                            request_body_info,
                            schema_definitions,
                        )
                        output_schemas = self._extract_output_schema_dependencies(
                            responses,
                            schema_definitions,
                        )

                        # Create initial route without pre-calculated fields
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
                            request_schemas=input_schemas,
                            response_schemas=output_schemas,
                            extensions=extensions,
                            openapi_version=self.openapi_version,
                        )

                        # Pre-calculate schema and parameter mapping for performance
                        try:
                            flat_schema, param_map = _combine_schemas_and_map_params(
                                route,
                                convert_refs=False,  # Parser already converted refs
                            )
                            route.flat_param_schema = flat_schema
                            route.parameter_map = param_map
                        except Exception as schema_error:
                            logger.warning(
                                f"Failed to pre-calculate schema for route {method_upper} {path_str}: {schema_error}"
                            )
                            # Continue with empty pre-calculated fields
                            route.flat_param_schema = {
                                "type": "object",
                                "properties": {},
                            }
                            route.parameter_map = {}
                        routes.append(route)
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

        logger.debug(f"Finished parsing. Extracted {len(routes)} HTTP routes.")
        return routes


# Export public symbols
__all__ = [
    "OpenAPIParser",
    "parse_openapi_to_http_routes",
]
