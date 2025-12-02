"""Parameter formatting functions for OpenAPI operations."""

import json
import logging
from typing import Any

from .models import JsonSchema, ParameterInfo, RequestBodyInfo

logger = logging.getLogger(__name__)


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


def format_deep_object_parameter(
    param_value: dict, parameter_name: str
) -> dict[str, str]:
    """
    Format a dictionary parameter for deep-object style serialization.

    According to OpenAPI 3.0 spec, deepObject style with explode=true serializes
    object properties as separate query parameters with bracket notation.

    For example, `{"id": "123", "type": "user"}` becomes
    `param[id]=123&param[type]=user`.

    Args:
        param_value: Dictionary value to format
        parameter_name: Name of the parameter

    Returns:
        Dictionary with bracketed parameter names as keys
    """
    if not isinstance(param_value, dict):
        logger.warning(
            f"Deep-object style parameter '{parameter_name}' expected dict, got {type(param_value)}"
        )
        return {}

    result = {}
    for key, value in param_value.items():
        # Format as param[key]=value
        bracketed_key = f"{parameter_name}[{key}]"
        result[bracketed_key] = str(value)

    return result


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
    """Formats Python data as a JSON string block for Markdown."""
    try:
        json_str = json.dumps(data, indent=indent)
        return f"```json\n{json_str}\n```"
    except TypeError:
        return f"```\nCould not serialize to JSON: {data}\n```"


def format_simple_description(
    base_description: str,
    parameters: list[ParameterInfo] | None = None,
    request_body: RequestBodyInfo | None = None,
) -> str:
    """
    Formats a simple description for MCP objects (tools, resources, prompts).
    Excludes response details, examples, and verbose status codes.

    Args:
        base_description (str): The initial description to be formatted.
        parameters (list[ParameterInfo] | None, optional): A list of parameter information.
        request_body (RequestBodyInfo | None, optional): Information about the request body.

    Returns:
        str: The formatted description string with minimal details.
    """
    desc_parts = [base_description]

    # Only add critical parameter information if they have descriptions
    if parameters:
        path_params = [p for p in parameters if p.location == "path" and p.description]
        if path_params:
            desc_parts.append("\n\n**Path Parameters:**")
            for param in path_params:
                desc_parts.append(f"\n- **{param.name}**: {param.description}")

    # Skip query parameters, request body details, and all response information
    # These are already captured in the inputSchema

    return "\n".join(desc_parts)


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


# Export public symbols
__all__ = [
    "format_array_parameter",
    "format_deep_object_parameter",
    "format_description_with_responses",
    "format_json_for_description",
    "format_simple_description",
    "generate_example_from_schema",
]
