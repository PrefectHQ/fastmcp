import json
import logging
from typing import Any, Literal, cast

# Using the recommended library: openapi-pydantic
from openapi_pydantic import (
    MediaType,
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

from fastmcp.utilities import openapi

logger = logging.getLogger(__name__)

# --- Intermediate Representation (IR) Definition ---
# (IR models remain the same)

HttpMethod = Literal[
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"
]
ParameterLocation = Literal["path", "query", "header", "cookie"]
JsonSchema = dict[str, Any]


class ParameterInfo(BaseModel):
    """Represents a single parameter for an HTTP operation in our IR."""

    name: str
    location: ParameterLocation  # Mapped from 'in' field of openapi-pydantic Parameter
    required: bool = False
    schema_: JsonSchema = Field(..., alias="schema")  # Target name in IR
    description: str | None = None

    # No model_config needed here if we populate manually after accessing 'in'


class RequestBodyInfo(BaseModel):
    """Represents the request body for an HTTP operation in our IR."""

    required: bool = False
    content_schema: dict[str, JsonSchema] = Field(
        default_factory=dict
    )  # Key: media type
    description: str | None = None


class ResponseInfo(BaseModel):
    """Represents response information in our IR."""

    description: str | None = None
    # Store schema per media type, key is media type
    content_schema: dict[str, JsonSchema] = Field(default_factory=dict)


class HTTPRoute(BaseModel):
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

# --- Helper Functions ---


def _resolve_ref(
    item: Reference | Schema | Parameter | RequestBody | Any, openapi: OpenAPI
) -> Any:
    """Resolves a potential Reference object to its target definition (no changes needed here)."""
    if isinstance(item, Reference):
        ref_str = item.ref
        try:
            if not ref_str.startswith("#/"):
                raise ValueError(
                    f"External or non-local reference not supported: {ref_str}"
                )
            parts = ref_str.strip("#/").split("/")
            target = openapi
            for part in parts:
                if part.isdigit() and isinstance(target, list):
                    target = target[int(part)]
                elif isinstance(target, BaseModel):
                    # Use model_extra for fields not explicitly defined (like components types)
                    # Check class fields first, then model_extra
                    if part in target.__class__.model_fields:
                        target = getattr(target, part, None)
                    elif target.model_extra and part in target.model_extra:
                        target = target.model_extra[part]
                    else:
                        # Special handling for components sub-types common structure
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
                        f"Cannot traverse part '{part}' in reference '{ref_str}' from type {type(target)}"
                    )
                if target is None:
                    raise ValueError(
                        f"Reference part '{part}' not found in path '{ref_str}'"
                    )
            if isinstance(target, Reference):
                return _resolve_ref(target, openapi)
            return target
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
            raise ValueError(f"Failed to resolve reference '{ref_str}': {e}") from e
    return item


# --- Main Parsing Function ---
# (No changes needed in the main loop logic, only in the helpers it calls)
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
            parser = OpenAPI30Parser(openapi_30)
            return parser.parse()
        else:
            # Default to OpenAPI 3.1 models
            openapi_31 = OpenAPI.model_validate(openapi_dict)
            logger.info(
                f"Successfully parsed OpenAPI 3.1 schema version: {openapi_31.openapi}"
            )
            parser = OpenAPI31Parser(openapi_31)
            return parser.parse()
    except ValidationError as e:
        logger.error(f"OpenAPI schema validation failed: {e}")
        error_details = e.errors()
        logger.error(f"Validation errors: {error_details}")
        raise ValueError(f"Invalid OpenAPI schema: {error_details}") from e


# Base parser class for shared functionality
class BaseOpenAPIParser:
    """Base class for OpenAPI parsers with common functionality."""

    def _convert_to_parameter_location(self, param_in: str) -> ParameterLocation:
        """Convert string parameter location to our ParameterLocation type."""
        if param_in == "path":
            return "path"
        elif param_in == "query":
            return "query"
        elif param_in == "header":
            return "header"
        elif param_in == "cookie":
            return "cookie"
        else:
            logger.warning(
                f"Unknown parameter location: {param_in}, defaulting to 'query'"
            )
            return "query"


class OpenAPI31Parser(BaseOpenAPIParser):
    """Parser for OpenAPI 3.1 schemas."""

    def __init__(self, openapi: OpenAPI):
        self.openapi = openapi

    def parse(self) -> list[HTTPRoute]:
        """Parse an OpenAPI 3.1 schema into HTTP routes."""
        routes: list[HTTPRoute] = []

        if not self.openapi.paths:
            logger.warning("OpenAPI schema has no paths defined.")
            return []

        for path_str, path_item_obj in self.openapi.paths.items():
            if not isinstance(path_item_obj, PathItem):
                logger.warning(
                    f"Skipping invalid path item object for path '{path_str}' (type: {type(path_item_obj)})"
                )
                continue

            path_level_params = path_item_obj.parameters

            # Iterate through possible HTTP methods defined in the PathItem model fields
            # Use model_fields from the class, not the instance
            for method_lower in PathItem.model_fields.keys():
                if method_lower not in [
                    "get",
                    "put",
                    "post",
                    "delete",
                    "options",
                    "head",
                    "patch",
                    "trace",
                ]:
                    continue

                operation: Operation | None = getattr(path_item_obj, method_lower, None)

                if operation and isinstance(operation, Operation):
                    method_upper = cast(HttpMethod, method_lower.upper())
                    logger.debug(f"Processing operation: {method_upper} {path_str}")
                    try:
                        parameters = self._extract_parameters(
                            operation.parameters, path_level_params
                        )
                        request_body_info = self._extract_request_body(
                            operation.requestBody
                        )
                        responses = self._extract_responses(operation.responses)

                        route = HTTPRoute(
                            path=path_str,
                            method=method_upper,
                            operation_id=operation.operationId,
                            summary=operation.summary,
                            description=operation.description,
                            tags=operation.tags or [],
                            parameters=parameters,
                            request_body=request_body_info,
                            responses=responses,
                        )
                        routes.append(route)
                        logger.info(
                            f"Successfully extracted route: {method_upper} {path_str}"
                        )
                    except Exception as op_error:
                        op_id = operation.operationId or "unknown"
                        logger.error(
                            f"Failed to process operation {method_upper} {path_str} (ID: {op_id}): {op_error}",
                            exc_info=True,
                        )

        logger.info(f"Finished parsing. Extracted {len(routes)} HTTP routes.")
        return routes

    def _resolve_ref(
        self, item: Reference | Schema | Parameter | RequestBody | Any
    ) -> Any:
        """Resolves a potential Reference object to its target definition."""
        if isinstance(item, Reference):
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
                        # Use model_extra for fields not explicitly defined (like components types)
                        # Check class fields first, then model_extra
                        if part in target.__class__.model_fields:
                            target = getattr(target, part, None)
                        elif target.model_extra and part in target.model_extra:
                            target = target.model_extra[part]
                        else:
                            # Special handling for components sub-types common structure
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
                            f"Cannot traverse part '{part}' in reference '{ref_str}' from type {type(target)}"
                        )
                    if target is None:
                        raise ValueError(
                            f"Reference part '{part}' not found in path '{ref_str}'"
                        )
                if isinstance(target, Reference):
                    return self._resolve_ref(target)
                return target
            except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
                raise ValueError(f"Failed to resolve reference '{ref_str}': {e}") from e
        return item

    def _extract_schema_as_dict(self, schema_obj: Schema | Reference) -> JsonSchema:
        """Resolves a schema/reference and returns it as a dictionary."""
        resolved_schema = self._resolve_ref(schema_obj)
        if isinstance(resolved_schema, Schema):
            # Using exclude_none=True might be better than exclude_unset sometimes
            return resolved_schema.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        elif isinstance(resolved_schema, dict):
            logger.warning(
                "Resolved schema reference resulted in a dict, not a Schema model."
            )
            return resolved_schema
        else:
            ref_str = getattr(schema_obj, "ref", "unknown")
            logger.warning(
                f"Expected Schema after resolving ref '{ref_str}', got {type(resolved_schema)}. Returning empty dict."
            )
            return {}

    def _extract_parameters(
        self,
        operation_params: list[Parameter | Reference] | None,
        path_item_params: list[Parameter | Reference] | None,
    ) -> list[ParameterInfo]:
        """Extracts and resolves parameters using corrected attribute names."""
        extracted_params: list[ParameterInfo] = []
        seen_params: dict[
            tuple[str, str], bool
        ] = {}  # Use string keys to avoid type issues
        all_params_refs = (operation_params or []) + (path_item_params or [])

        for param_or_ref in all_params_refs:
            try:
                parameter = cast(Parameter, self._resolve_ref(param_or_ref))
                if not isinstance(parameter, Parameter):
                    # ... (error logging remains the same)
                    continue

                # --- *** CORRECTED ATTRIBUTE ACCESS HERE *** ---
                param_in = parameter.param_in  # CORRECTED: Use 'param_in'
                param_location = self._convert_to_parameter_location(param_in)
                param_schema_obj = (
                    parameter.param_schema
                )  # CORRECTED: Use 'param_schema'
                # --- *** ---

                param_key = (parameter.name, param_in)
                if param_key in seen_params:
                    continue
                seen_params[param_key] = True

                param_schema_dict = {}
                if param_schema_obj:  # Check if schema exists
                    param_schema_dict = self._extract_schema_as_dict(param_schema_obj)
                elif parameter.content:
                    # Handle complex parameters with 'content'
                    first_media_type = next(iter(parameter.content.values()), None)
                    if (
                        first_media_type and first_media_type.media_type_schema
                    ):  # CORRECTED: Use 'media_type_schema'
                        param_schema_dict = self._extract_schema_as_dict(
                            first_media_type.media_type_schema
                        )
                        logger.debug(
                            f"Parameter '{parameter.name}' using schema from 'content' field."
                        )

                # Manually create ParameterInfo instance using correct field names
                param_info = ParameterInfo(
                    name=parameter.name,
                    location=param_location,  # Use converted parameter location
                    required=parameter.required,
                    schema=param_schema_dict,  # Populate 'schema' field in IR
                    description=parameter.description,
                )
                extracted_params.append(param_info)

            except (
                ValidationError,
                ValueError,
                AttributeError,
                TypeError,
            ) as e:  # Added TypeError
                param_name = getattr(
                    param_or_ref, "name", getattr(param_or_ref, "ref", "unknown")
                )
                logger.error(
                    f"Failed to extract parameter '{param_name}': {e}", exc_info=False
                )

        return extracted_params

    def _extract_request_body(
        self, request_body_or_ref: RequestBody | Reference | None
    ) -> RequestBodyInfo | None:
        """Extracts and resolves the request body using corrected attribute names."""
        if not request_body_or_ref:
            return None
        try:
            request_body = cast(RequestBody, self._resolve_ref(request_body_or_ref))
            if not isinstance(request_body, RequestBody):
                # ... (error logging remains the same)
                return None

            content_schemas: dict[str, JsonSchema] = {}
            if request_body.content:
                for media_type_str, media_type_obj in request_body.content.items():
                    # --- *** CORRECTED ATTRIBUTE ACCESS HERE *** ---
                    if (
                        isinstance(media_type_obj, MediaType)
                        and media_type_obj.media_type_schema
                    ):  # CORRECTED: Use 'media_type_schema'
                        # --- *** ---
                        try:
                            # Use the corrected attribute here as well
                            schema_dict = self._extract_schema_as_dict(
                                media_type_obj.media_type_schema
                            )
                            content_schemas[media_type_str] = schema_dict
                        except ValueError as schema_err:
                            logger.error(
                                f"Failed to extract schema for media type '{media_type_str}' in request body: {schema_err}"
                            )
                    elif not isinstance(media_type_obj, MediaType):
                        logger.warning(
                            f"Skipping invalid media type object for '{media_type_str}' (type: {type(media_type_obj)}) in request body."
                        )
                    elif not media_type_obj.media_type_schema:  # Corrected check
                        logger.warning(
                            f"Skipping media type '{media_type_str}' in request body because it lacks a schema."
                        )

            return RequestBodyInfo(
                required=request_body.required,
                content_schema=content_schemas,
                description=request_body.description,
            )
        except (ValidationError, ValueError, AttributeError) as e:
            ref_name = getattr(request_body_or_ref, "ref", "unknown")
            logger.error(
                f"Failed to extract request body '{ref_name}': {e}", exc_info=False
            )
            return None

    def _extract_responses(
        self,
        operation_responses: dict[str, Response | Reference] | None,
    ) -> dict[str, ResponseInfo]:
        """Extracts and resolves response information for an operation."""
        extracted_responses: dict[str, ResponseInfo] = {}
        if not operation_responses:
            return extracted_responses

        for status_code, resp_or_ref in operation_responses.items():
            try:
                response = cast(Response, self._resolve_ref(resp_or_ref))
                if not isinstance(response, Response):
                    ref_str = getattr(resp_or_ref, "ref", "unknown")
                    logger.warning(
                        f"Expected Response after resolving ref '{ref_str}' for status code {status_code}, got {type(response)}. Skipping."
                    )
                    continue

                content_schemas: dict[str, JsonSchema] = {}
                if response.content:
                    for media_type_str, media_type_obj in response.content.items():
                        if (
                            isinstance(media_type_obj, MediaType)
                            and media_type_obj.media_type_schema
                        ):
                            try:
                                schema_dict = self._extract_schema_as_dict(
                                    media_type_obj.media_type_schema
                                )
                                content_schemas[media_type_str] = schema_dict
                            except ValueError as schema_err:
                                logger.error(
                                    f"Failed to extract schema for media type '{media_type_str}' in response {status_code}: {schema_err}"
                                )

                resp_info = ResponseInfo(
                    description=response.description, content_schema=content_schemas
                )
                extracted_responses[str(status_code)] = resp_info

            except (ValidationError, ValueError, AttributeError) as e:
                ref_name = getattr(resp_or_ref, "ref", "unknown")
                logger.error(
                    f"Failed to extract response for status code {status_code} "
                    f"from reference '{ref_name}': {e}",
                    exc_info=False,
                )

        return extracted_responses


class OpenAPI30Parser(BaseOpenAPIParser):
    """Parser for OpenAPI 3.0 schemas."""

    def __init__(self, openapi: OpenAPI_30):
        self.openapi = openapi

    def parse(self) -> list[HTTPRoute]:
        """Parse an OpenAPI 3.0 schema into HTTP routes."""
        routes: list[HTTPRoute] = []

        if not self.openapi.paths:
            logger.warning("OpenAPI schema has no paths defined.")
            return []

        for path_str, path_item_obj in self.openapi.paths.items():
            if not isinstance(path_item_obj, PathItem_30):
                logger.warning(
                    f"Skipping invalid path item object for path '{path_str}' (type: {type(path_item_obj)})"
                )
                continue

            path_level_params = path_item_obj.parameters

            # Iterate through possible HTTP methods defined in the PathItem model fields
            # Use model_fields from the class, not the instance
            for method_lower in PathItem_30.model_fields.keys():
                if method_lower not in [
                    "get",
                    "put",
                    "post",
                    "delete",
                    "options",
                    "head",
                    "patch",
                    "trace",
                ]:
                    continue

                operation: Operation_30 | None = getattr(
                    path_item_obj, method_lower, None
                )

                if operation and isinstance(operation, Operation_30):
                    method_upper = cast(HttpMethod, method_lower.upper())
                    logger.debug(f"Processing operation: {method_upper} {path_str}")
                    try:
                        parameters = self._extract_parameters(
                            operation.parameters, path_level_params
                        )
                        request_body_info = self._extract_request_body(
                            operation.requestBody
                        )
                        responses = self._extract_responses(operation.responses)

                        route = HTTPRoute(
                            path=path_str,
                            method=method_upper,
                            operation_id=operation.operationId,
                            summary=operation.summary,
                            description=operation.description,
                            tags=operation.tags or [],
                            parameters=parameters,
                            request_body=request_body_info,
                            responses=responses,
                        )
                        routes.append(route)
                        logger.info(
                            f"Successfully extracted route: {method_upper} {path_str}"
                        )
                    except Exception as op_error:
                        op_id = operation.operationId or "unknown"
                        logger.error(
                            f"Failed to process operation {method_upper} {path_str} (ID: {op_id}): {op_error}",
                            exc_info=True,
                        )

        logger.info(f"Finished parsing. Extracted {len(routes)} HTTP routes.")
        return routes

    def _resolve_ref(
        self, item: Reference_30 | Schema_30 | Parameter_30 | RequestBody_30 | Any
    ) -> Any:
        """Resolves a potential Reference object to its target definition for OpenAPI 3.0."""
        if isinstance(item, Reference_30):
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
                        # Use model_extra for fields not explicitly defined (like components types)
                        # Check class fields first, then model_extra
                        if part in target.__class__.model_fields:
                            target = getattr(target, part, None)
                        elif target.model_extra and part in target.model_extra:
                            target = target.model_extra[part]
                        else:
                            # Special handling for components sub-types common structure
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
                            f"Cannot traverse part '{part}' in reference '{ref_str}' from type {type(target)}"
                        )
                    if target is None:
                        raise ValueError(
                            f"Reference part '{part}' not found in path '{ref_str}'"
                        )
                if isinstance(target, Reference_30):
                    return self._resolve_ref(target)
                return target
            except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
                raise ValueError(f"Failed to resolve reference '{ref_str}': {e}") from e
        return item

    def _extract_schema_as_dict(
        self, schema_obj: Schema_30 | Reference_30
    ) -> JsonSchema:
        """Resolves a schema/reference and returns it as a dictionary for OpenAPI 3.0."""
        resolved_schema = self._resolve_ref(schema_obj)
        if isinstance(resolved_schema, Schema_30):
            # Using exclude_none=True might be better than exclude_unset sometimes
            return resolved_schema.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        elif isinstance(resolved_schema, dict):
            logger.warning(
                "Resolved schema reference resulted in a dict, not a Schema model."
            )
            return resolved_schema
        else:
            ref_str = getattr(schema_obj, "ref", "unknown")
            logger.warning(
                f"Expected Schema after resolving ref '{ref_str}', got {type(resolved_schema)}. Returning empty dict."
            )
            return {}

    def _extract_parameters(
        self,
        operation_params: list[Parameter_30 | Reference_30] | None,
        path_item_params: list[Parameter_30 | Reference_30] | None,
    ) -> list[ParameterInfo]:
        """Extracts and resolves parameters for OpenAPI 3.0."""
        extracted_params: list[ParameterInfo] = []
        seen_params: dict[
            tuple[str, str], bool
        ] = {}  # Use string keys to avoid type issues
        all_params_refs = (operation_params or []) + (path_item_params or [])

        for param_or_ref in all_params_refs:
            try:
                parameter = cast(Parameter_30, self._resolve_ref(param_or_ref))
                if not isinstance(parameter, Parameter_30):
                    logger.warning(
                        f"Expected Parameter after resolving reference, got {type(parameter)}. Skipping."
                    )
                    continue

                # OpenAPI 3.0 uses 'in' field for parameter location
                param_in = parameter.param_in
                param_location = self._convert_to_parameter_location(param_in)
                param_schema_obj = parameter.param_schema

                param_key = (parameter.name, param_in)
                if param_key in seen_params:
                    continue
                seen_params[param_key] = True

                param_schema_dict = {}
                if param_schema_obj:  # Check if schema exists
                    param_schema_dict = self._extract_schema_as_dict(param_schema_obj)
                elif parameter.content:
                    # Handle complex parameters with 'content'
                    first_media_type = next(iter(parameter.content.values()), None)
                    if first_media_type and first_media_type.media_type_schema:
                        param_schema_dict = self._extract_schema_as_dict(
                            first_media_type.media_type_schema
                        )
                        logger.debug(
                            f"Parameter '{parameter.name}' using schema from 'content' field."
                        )

                # Manually create ParameterInfo instance using correct field names
                param_info = ParameterInfo(
                    name=parameter.name,
                    location=param_location,  # Use converted parameter location
                    required=parameter.required,
                    schema=param_schema_dict,  # Populate 'schema' field in IR
                    description=parameter.description,
                )
                extracted_params.append(param_info)

            except (
                ValidationError,
                ValueError,
                AttributeError,
                TypeError,
            ) as e:  # Added TypeError
                param_name = getattr(
                    param_or_ref, "name", getattr(param_or_ref, "ref", "unknown")
                )
                logger.error(
                    f"Failed to extract parameter '{param_name}': {e}", exc_info=False
                )

        return extracted_params

    def _extract_request_body(
        self, request_body_or_ref: RequestBody_30 | Reference_30 | None
    ) -> RequestBodyInfo | None:
        """Extracts request body information for OpenAPI 3.0 using correct attribute names."""
        if request_body_or_ref is None:
            return None

        try:
            request_body = cast(RequestBody_30, self._resolve_ref(request_body_or_ref))

            if not isinstance(request_body, RequestBody_30):
                logger.warning(
                    f"Expected RequestBody after resolving reference, got {type(request_body)}. Returning None."
                )
                return None

            request_body_info = RequestBodyInfo(
                required=request_body.required,
                description=request_body.description,
            )

            # Process content field for request body schemas
            if request_body.content:
                for media_type_key, media_type_obj in request_body.content.items():
                    if (
                        media_type_obj and media_type_obj.media_type_schema
                    ):  # CORRECTED: Use 'media_type_schema'
                        schema_dict = self._extract_schema_as_dict(
                            media_type_obj.media_type_schema
                        )
                        request_body_info.content_schema[media_type_key] = schema_dict

            return request_body_info

        except (ValidationError, ValueError, AttributeError) as e:
            ref_str = getattr(request_body_or_ref, "ref", "unknown")
            logger.error(
                f"Failed to extract request body info from reference '{ref_str}': {e}",
                exc_info=False,
            )
            return None

    def _extract_responses(
        self,
        operation_responses: dict[str, Response_30 | Reference_30] | None,
    ) -> dict[str, ResponseInfo]:
        """Extracts response information from an OpenAPI 3.0 operation's responses."""
        extracted_responses: dict[str, ResponseInfo] = {}
        if not operation_responses:
            return extracted_responses

        for status_code, response_or_ref in operation_responses.items():
            try:
                # Skip 'default' response for simplicity if needed
                # if status_code == "default":
                #    continue

                response = cast(Response_30, self._resolve_ref(response_or_ref))

                if not isinstance(response, Response_30):
                    logger.warning(
                        f"Expected Response after resolving reference for status code {status_code}, "
                        f"got {type(response)}. Skipping."
                    )
                    continue

                response_info = ResponseInfo(description=response.description)

                # Extract content schemas if present
                if response.content:
                    for media_type_key, media_type_obj in response.content.items():
                        if (
                            media_type_obj and media_type_obj.media_type_schema
                        ):  # CORRECTED: Use 'media_type_schema'
                            schema_dict = self._extract_schema_as_dict(
                                media_type_obj.media_type_schema
                            )
                            response_info.content_schema[media_type_key] = schema_dict

                extracted_responses[status_code] = response_info

            except (ValidationError, ValueError, AttributeError) as e:
                ref_str = getattr(response_or_ref, "ref", "unknown")
                logger.error(
                    f"Failed to extract response info for status code {status_code} "
                    f"from reference '{ref_str}': {e}",
                    exc_info=False,
                )

        return extracted_responses


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

    # Remove title if it just repeats the property name (heuristic)
    # This requires knowing the property name, so better done when formatting properties dict

    return cleaned


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
) -> str:
    """Formats the base description string with response information."""
    if not responses:
        return base_description

    desc_parts = [base_description]
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

                if schema:
                    # Generate Example
                    example = generate_example_from_schema(schema)
                    if example != "unknown_type" and example is not None:
                        desc_parts.append("\n  - **Example:**")
                        desc_parts.append(
                            format_json_for_description(example, indent=2)
                        )

    return "\n".join(desc_parts)


def _combine_schemas(route: openapi.HTTPRoute) -> dict[str, Any]:
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
        properties[param.name] = param.schema_

    # Add request body if it exists
    if route.request_body and route.request_body.content_schema:
        # For now, just use the first content type's schema
        content_type = next(iter(route.request_body.content_schema))
        body_schema = route.request_body.content_schema[content_type]
        body_props = body_schema.get("properties", {})
        for prop_name, prop_schema in body_props.items():
            properties[prop_name] = prop_schema
        if route.request_body.required:
            required.extend(body_schema.get("required", []))

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
