"""Schema manipulation utilities for OpenAPI operations."""

from typing import Any

from fastmcp.utilities.logging import get_logger

from .models import HTTPRoute, JsonSchema, ResponseInfo

logger = get_logger(__name__)


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

    return cleaned


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
        if isinstance(ref_path, str):
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


def _make_optional_parameter_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Make an optional parameter schema nullable to allow None values.

    For optional parameters, we need to allow null values in addition to the
    specified type to handle cases where None is passed for optional parameters.
    """
    # If schema already has multiple types or is already nullable, don't modify
    if "anyOf" in schema or "oneOf" in schema or "allOf" in schema:
        return schema

    # If it's already nullable (type includes null), don't modify
    if isinstance(schema.get("type"), list) and "null" in schema["type"]:
        return schema

    # Create a new schema that allows null in addition to the original type
    if "type" in schema:
        original_type = schema["type"]
        if isinstance(original_type, str):
            # Handle different types appropriately
            if original_type in ("array", "object"):
                # For complex types (array/object), preserve the full structure
                # and allow null as an alternative
                if original_type == "array" and "items" in schema:
                    # Array with items - preserve items in anyOf branch
                    array_schema = schema.copy()
                    top_level_fields = ["default", "description", "title", "example"]
                    nullable_schema = {}

                    # Move top-level fields to the root
                    for field in top_level_fields:
                        if field in array_schema:
                            nullable_schema[field] = array_schema.pop(field)

                    nullable_schema["anyOf"] = [array_schema, {"type": "null"}]
                    return nullable_schema

                elif original_type == "object" and "properties" in schema:
                    # Object with properties - preserve properties in anyOf branch
                    object_schema = schema.copy()
                    top_level_fields = ["default", "description", "title", "example"]
                    nullable_schema = {}

                    # Move top-level fields to the root
                    for field in top_level_fields:
                        if field in object_schema:
                            nullable_schema[field] = object_schema.pop(field)

                    nullable_schema["anyOf"] = [object_schema, {"type": "null"}]
                    return nullable_schema
                else:
                    # Simple object/array without items/properties
                    nullable_schema = {}
                    original_schema = schema.copy()
                    top_level_fields = ["default", "description", "title", "example"]

                    for field in top_level_fields:
                        if field in original_schema:
                            nullable_schema[field] = original_schema.pop(field)

                    nullable_schema["anyOf"] = [original_schema, {"type": "null"}]
                    return nullable_schema
            else:
                # Simple types (string, integer, number, boolean)
                top_level_fields = ["default", "description", "title", "example"]
                nullable_schema = {}
                original_schema = schema.copy()

                for field in top_level_fields:
                    if field in original_schema:
                        nullable_schema[field] = original_schema.pop(field)

                nullable_schema["anyOf"] = [original_schema, {"type": "null"}]
                return nullable_schema

    return schema


def _combine_schemas_and_map_params(
    route: HTTPRoute,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """
    Combines parameter and request body schemas into a single schema.
    Handles parameter name collisions by adding location suffixes.
    Also returns parameter mapping for request director.

    Args:
        route: HTTPRoute object

    Returns:
        Tuple of (combined schema dictionary, parameter mapping)
        Parameter mapping format: {'flat_arg_name': {'location': 'path', 'openapi_name': 'id'}}
    """
    properties = {}
    required = []
    parameter_map = {}  # Track mapping from flat arg names to OpenAPI locations

    # First pass: collect parameter names by location and body properties
    param_names_by_location = {
        "path": set(),
        "query": set(),
        "header": set(),
        "cookie": set(),
    }
    body_props = {}

    for param in route.parameters:
        param_names_by_location[param.location].add(param.name)

    if route.request_body and route.request_body.content_schema:
        content_type = next(iter(route.request_body.content_schema))
        body_schema = _replace_ref_with_defs(
            route.request_body.content_schema[content_type].copy(),
            route.request_body.description,
        )
        body_props = body_schema.get("properties", {})

    # Detect collisions: parameters that exist in both body and path/query/header
    all_non_body_params = set()
    for location_params in param_names_by_location.values():
        all_non_body_params.update(location_params)

    body_param_names = set(body_props.keys())
    colliding_params = all_non_body_params & body_param_names

    # Add parameters with suffixes for collisions
    for param in route.parameters:
        if param.name in colliding_params:
            # Add suffix for non-body parameters when collision detected
            suffixed_name = f"{param.name}__{param.location}"
            if param.required:
                required.append(suffixed_name)

            # Track parameter mapping
            parameter_map[suffixed_name] = {
                "location": param.location,
                "openapi_name": param.name,
            }

            # Add location info to description
            param_schema = _replace_ref_with_defs(
                param.schema_.copy(), param.description
            )
            original_desc = param_schema.get("description", "")
            location_desc = f"({param.location.capitalize()} parameter)"
            if original_desc:
                param_schema["description"] = f"{original_desc} {location_desc}"
            else:
                param_schema["description"] = location_desc

            # Don't make optional parameters nullable - they can simply be omitted
            # The OpenAPI specification doesn't require optional parameters to accept null values

            properties[suffixed_name] = param_schema
        else:
            # No collision, use original name
            if param.required:
                required.append(param.name)

            # Track parameter mapping
            parameter_map[param.name] = {
                "location": param.location,
                "openapi_name": param.name,
            }

            param_schema = _replace_ref_with_defs(
                param.schema_.copy(), param.description
            )

            # Don't make optional parameters nullable - they can simply be omitted
            # The OpenAPI specification doesn't require optional parameters to accept null values

            properties[param.name] = param_schema

    # Add request body properties (no suffixes for body parameters)
    if route.request_body and route.request_body.content_schema:
        for prop_name, prop_schema in body_props.items():
            properties[prop_name] = prop_schema

            # Track parameter mapping for body properties
            parameter_map[prop_name] = {"location": "body", "openapi_name": prop_name}

        if route.request_body.required:
            required.extend(body_schema.get("required", []))

    result = {
        "type": "object",
        "properties": properties,
        "required": required,
    }
    # Add schema definitions if available
    if route.schema_definitions:
        result["$defs"] = route.schema_definitions.copy()

    # Use lightweight compression - prune additionalProperties and unused definitions
    if result.get("additionalProperties") is False:
        result.pop("additionalProperties")

    # Remove unused definitions (lightweight approach - just check direct $ref usage)
    if "$defs" in result:
        used_refs = set()

        def find_refs_in_value(value):
            if isinstance(value, dict):
                if "$ref" in value and isinstance(value["$ref"], str):
                    ref = value["$ref"]
                    if ref.startswith("#/$defs/"):
                        used_refs.add(ref.split("/")[-1])
                for v in value.values():
                    find_refs_in_value(v)
            elif isinstance(value, list):
                for item in value:
                    find_refs_in_value(item)

        # Find refs in the main schema (excluding $defs section)
        for key, value in result.items():
            if key != "$defs":
                find_refs_in_value(value)

        # Remove unused definitions
        if used_refs:
            result["$defs"] = {
                name: def_schema
                for name, def_schema in result["$defs"].items()
                if name in used_refs
            }
        else:
            result.pop("$defs")

    return result, parameter_map


def _combine_schemas(route: HTTPRoute) -> dict[str, Any]:
    """
    Combines parameter and request body schemas into a single schema.
    Handles parameter name collisions by adding location suffixes.

    This is a backward compatibility wrapper around _combine_schemas_and_map_params.

    Args:
        route: HTTPRoute object

    Returns:
        Combined schema dictionary
    """
    schema, _ = _combine_schemas_and_map_params(route)
    return schema


def extract_output_schema_from_responses(
    responses: dict[str, ResponseInfo],
    schema_definitions: dict[str, Any] | None = None,
    openapi_version: str | None = None,
) -> dict[str, Any] | None:
    """
    Extract output schema from OpenAPI responses for use as MCP tool output schema.

    This function finds the first successful response (200, 201, 202, 204) with a
    JSON-compatible content type and extracts its schema. If the schema is not an
    object type, it wraps it to comply with MCP requirements.

    Args:
        responses: Dictionary of ResponseInfo objects keyed by status code
        schema_definitions: Optional schema definitions to include in the output schema
        openapi_version: OpenAPI version string, used to optimize nullable field handling

    Returns:
        dict: MCP-compliant output schema with potential wrapping, or None if no suitable schema found
    """
    if not responses:
        return None

    # Priority order for success status codes
    success_codes = ["200", "201", "202", "204"]

    # Find the first successful response
    response_info = None
    for status_code in success_codes:
        if status_code in responses:
            response_info = responses[status_code]
            break

    # If no explicit success codes, try any 2xx response
    if response_info is None:
        for status_code, resp_info in responses.items():
            if status_code.startswith("2"):
                response_info = resp_info
                break

    if response_info is None or not response_info.content_schema:
        return None

    # Prefer application/json, then fall back to other JSON-compatible types
    json_compatible_types = [
        "application/json",
        "application/vnd.api+json",
        "application/hal+json",
        "application/ld+json",
        "text/json",
    ]

    schema = None
    for content_type in json_compatible_types:
        if content_type in response_info.content_schema:
            schema = response_info.content_schema[content_type]
            break

    # If no JSON-compatible type found, try the first available content type
    if schema is None and response_info.content_schema:
        first_content_type = next(iter(response_info.content_schema))
        schema = response_info.content_schema[first_content_type]
        logger.debug(
            f"Using non-JSON content type for output schema: {first_content_type}"
        )

    if not schema or not isinstance(schema, dict):
        return None

    # Clean and copy the schema
    output_schema = schema.copy()

    # If schema has a $ref, resolve it first before processing nullable fields
    if "$ref" in output_schema and schema_definitions:
        ref_path = output_schema["$ref"]
        if ref_path.startswith("#/components/schemas/"):
            schema_name = ref_path.split("/")[-1]
            if schema_name in schema_definitions:
                # Replace $ref with the actual schema definition
                output_schema = schema_definitions[schema_name].copy()

    # Convert OpenAPI schema to JSON Schema format
    # Only needed for OpenAPI 3.0 - 3.1 uses standard JSON Schema null types
    if openapi_version and openapi_version.startswith("3.0"):
        from .json_schema_converter import convert_openapi_schema_to_json_schema

        output_schema = convert_openapi_schema_to_json_schema(
            output_schema, openapi_version
        )

    # MCP requires output schemas to be objects. If this schema is not an object,
    # we need to wrap it similar to how ParsedFunction.from_function() does it
    if output_schema.get("type") != "object":
        # Create a wrapped schema that contains the original schema under a "result" key
        wrapped_schema = {
            "type": "object",
            "properties": {"result": output_schema},
            "required": ["result"],
            "x-fastmcp-wrap-result": True,
        }
        output_schema = wrapped_schema

    # Add schema definitions if available and handle nullable fields in them
    # Only add $defs if we didn't resolve the $ref inline above
    if schema_definitions and "$ref" not in schema.copy():
        processed_defs = {}
        for def_name, def_schema in schema_definitions.items():
            # Convert OpenAPI schema definitions to JSON Schema format
            if openapi_version and openapi_version.startswith("3.0"):
                from .json_schema_converter import convert_openapi_schema_to_json_schema

                processed_defs[def_name] = convert_openapi_schema_to_json_schema(
                    def_schema, openapi_version
                )
            else:
                processed_defs[def_name] = def_schema
        output_schema["$defs"] = processed_defs

    # Use lightweight compression - prune additionalProperties and unused definitions
    if output_schema.get("additionalProperties") is False:
        output_schema.pop("additionalProperties")

    # Remove unused definitions (lightweight approach - just check direct $ref usage)
    if "$defs" in output_schema:
        used_refs = set()

        def find_refs_in_value(value):
            if isinstance(value, dict):
                if "$ref" in value and isinstance(value["$ref"], str):
                    ref = value["$ref"]
                    if ref.startswith("#/$defs/"):
                        used_refs.add(ref.split("/")[-1])
                for v in value.values():
                    find_refs_in_value(v)
            elif isinstance(value, list):
                for item in value:
                    find_refs_in_value(item)

        # Find refs in the main schema (excluding $defs section)
        for key, value in output_schema.items():
            if key != "$defs":
                find_refs_in_value(value)

        # Remove unused definitions
        if used_refs:
            output_schema["$defs"] = {
                name: def_schema
                for name, def_schema in output_schema["$defs"].items()
                if name in used_refs
            }
        else:
            output_schema.pop("$defs")

    return output_schema


# Export public symbols
__all__ = [
    "clean_schema_for_display",
    "_combine_schemas",
    "_combine_schemas_and_map_params",
    "extract_output_schema_from_responses",
    "_replace_ref_with_defs",
    "_make_optional_parameter_nullable",
]
