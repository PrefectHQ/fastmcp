"""
Clean OpenAPI 3.0 to JSON Schema converter for the experimental parser.

This module provides a systematic approach to converting OpenAPI 3.0 schemas
to JSON Schema, inspired by py-openapi-schema-to-json-schema but optimized
for our specific use case.
"""

from typing import Any

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# OpenAPI-specific fields that should be removed from JSON Schema
OPENAPI_SPECIFIC_FIELDS = {
    "nullable",  # Handled by converting to type arrays
    "discriminator",  # OpenAPI-specific
    "readOnly",  # OpenAPI-specific metadata
    "writeOnly",  # OpenAPI-specific metadata
    "xml",  # OpenAPI-specific metadata
    "externalDocs",  # OpenAPI-specific metadata
    "deprecated",  # Can be kept but not part of JSON Schema core
}

# Fields that should be recursively processed
RECURSIVE_FIELDS = {
    "properties": dict,
    "items": dict,
    "additionalProperties": dict,
    "allOf": list,
    "anyOf": list,
    "oneOf": list,
    "not": dict,
}


def convert_openapi_schema_to_json_schema(
    schema: dict[str, Any],
    openapi_version: str | None = None,
    remove_read_only: bool = False,
    remove_write_only: bool = False,
    convert_one_of_to_any_of: bool = True,
) -> dict[str, Any]:
    """
    Convert an OpenAPI schema to JSON Schema format.

    This is a clean, systematic approach that:
    1. Removes OpenAPI-specific fields
    2. Converts nullable fields to type arrays (for OpenAPI 3.0 only)
    3. Converts oneOf to anyOf for overlapping union handling
    4. Recursively processes nested schemas
    5. Optionally removes readOnly/writeOnly properties

    Args:
        schema: OpenAPI schema dictionary
        openapi_version: OpenAPI version for optimization
        remove_read_only: Whether to remove readOnly properties
        remove_write_only: Whether to remove writeOnly properties
        convert_one_of_to_any_of: Whether to convert oneOf to anyOf

    Returns:
        JSON Schema compatible dictionary
    """
    if not isinstance(schema, dict):
        return schema

    # Early exit optimization - check if conversion is needed
    needs_conversion = (
        any(field in schema for field in OPENAPI_SPECIFIC_FIELDS)
        or (remove_read_only and _has_read_only_properties(schema))
        or (remove_write_only and _has_write_only_properties(schema))
        or (convert_one_of_to_any_of and "oneOf" in schema)
        or _needs_recursive_processing(
            schema,
            openapi_version,
            remove_read_only,
            remove_write_only,
            convert_one_of_to_any_of,
        )
    )

    if not needs_conversion:
        return schema

    # Work on a copy to avoid mutation
    result = schema.copy()

    # Step 1: Handle nullable field conversion (OpenAPI 3.0 only)
    if openapi_version and openapi_version.startswith("3.0"):
        result = _convert_nullable_field(result)

    # Step 2: Convert oneOf to anyOf if requested
    if convert_one_of_to_any_of and "oneOf" in result:
        result["anyOf"] = result.pop("oneOf")

    # Step 3: Remove OpenAPI-specific fields
    for field in OPENAPI_SPECIFIC_FIELDS:
        result.pop(field, None)

    # Step 4: Handle readOnly/writeOnly property removal
    if remove_read_only or remove_write_only:
        result = _filter_properties_by_access(
            result, remove_read_only, remove_write_only
        )

    # Step 5: Recursively process nested schemas
    for field_name, field_type in RECURSIVE_FIELDS.items():
        if field_name in result:
            if field_type is dict and isinstance(result[field_name], dict):
                if field_name == "properties":
                    # Handle properties specially - each property is a schema
                    result[field_name] = {
                        prop_name: convert_openapi_schema_to_json_schema(
                            prop_schema,
                            openapi_version,
                            remove_read_only,
                            remove_write_only,
                            convert_one_of_to_any_of,
                        )
                        if isinstance(prop_schema, dict)
                        else prop_schema
                        for prop_name, prop_schema in result[field_name].items()
                    }
                else:
                    result[field_name] = convert_openapi_schema_to_json_schema(
                        result[field_name],
                        openapi_version,
                        remove_read_only,
                        remove_write_only,
                        convert_one_of_to_any_of,
                    )
            elif field_type is list and isinstance(result[field_name], list):
                result[field_name] = [
                    convert_openapi_schema_to_json_schema(
                        item,
                        openapi_version,
                        remove_read_only,
                        remove_write_only,
                        convert_one_of_to_any_of,
                    )
                    if isinstance(item, dict)
                    else item
                    for item in result[field_name]
                ]

    return result


def _convert_nullable_field(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAPI nullable field to JSON Schema type array."""
    if "nullable" not in schema:
        return schema

    result = schema.copy()
    nullable_value = result.pop("nullable")

    # Only convert if nullable is True and we have a type structure
    if not nullable_value:
        return result

    if "type" in result:
        current_type = result["type"]
        if isinstance(current_type, str):
            result["type"] = [current_type, "null"]
        elif isinstance(current_type, list) and "null" not in current_type:
            result["type"] = [*current_type, "null"]
    elif "oneOf" in result:
        # Convert oneOf to anyOf with null
        result["anyOf"] = [*result.pop("oneOf"), {"type": "null"}]
    elif "anyOf" in result:
        # Add null to anyOf if not present
        if not any(item.get("type") == "null" for item in result["anyOf"]):
            result["anyOf"].append({"type": "null"})
    elif "allOf" in result:
        # Wrap allOf in anyOf with null option
        result["anyOf"] = [{"allOf": result.pop("allOf")}, {"type": "null"}]

    # Handle enum fields - add null to enum values if present
    if "enum" in result and None not in result["enum"]:
        result["enum"] = result["enum"] + [None]

    return result


def _has_read_only_properties(schema: dict[str, Any]) -> bool:
    """Quick check if schema has any readOnly properties."""
    if "properties" not in schema:
        return False
    return any(
        isinstance(prop, dict) and prop.get("readOnly")
        for prop in schema["properties"].values()
    )


def _has_write_only_properties(schema: dict[str, Any]) -> bool:
    """Quick check if schema has any writeOnly properties."""
    if "properties" not in schema:
        return False
    return any(
        isinstance(prop, dict) and prop.get("writeOnly")
        for prop in schema["properties"].values()
    )


def _needs_recursive_processing(
    schema: dict[str, Any],
    openapi_version: str | None,
    remove_read_only: bool,
    remove_write_only: bool,
    convert_one_of_to_any_of: bool,
) -> bool:
    """Check if the schema needs recursive processing (smarter than just checking for recursive fields)."""
    for field_name, field_type in RECURSIVE_FIELDS.items():
        if field_name in schema:
            if field_type is dict and isinstance(schema[field_name], dict):
                if field_name == "properties":
                    # Check if any property needs conversion
                    for prop_schema in schema[field_name].values():
                        if isinstance(prop_schema, dict):
                            nested_needs_conversion = (
                                any(
                                    field in prop_schema
                                    for field in OPENAPI_SPECIFIC_FIELDS
                                )
                                or (remove_read_only and prop_schema.get("readOnly"))
                                or (remove_write_only and prop_schema.get("writeOnly"))
                                or (convert_one_of_to_any_of and "oneOf" in prop_schema)
                                or _needs_recursive_processing(
                                    prop_schema,
                                    openapi_version,
                                    remove_read_only,
                                    remove_write_only,
                                    convert_one_of_to_any_of,
                                )
                            )
                            if nested_needs_conversion:
                                return True
                else:
                    # Check if nested schema needs conversion
                    nested_needs_conversion = (
                        any(
                            field in schema[field_name]
                            for field in OPENAPI_SPECIFIC_FIELDS
                        )
                        or (
                            remove_read_only
                            and _has_read_only_properties(schema[field_name])
                        )
                        or (
                            remove_write_only
                            and _has_write_only_properties(schema[field_name])
                        )
                        or (convert_one_of_to_any_of and "oneOf" in schema[field_name])
                        or _needs_recursive_processing(
                            schema[field_name],
                            openapi_version,
                            remove_read_only,
                            remove_write_only,
                            convert_one_of_to_any_of,
                        )
                    )
                    if nested_needs_conversion:
                        return True
            elif field_type is list and isinstance(schema[field_name], list):
                # Check if any list item needs conversion
                for item in schema[field_name]:
                    if isinstance(item, dict):
                        nested_needs_conversion = (
                            any(field in item for field in OPENAPI_SPECIFIC_FIELDS)
                            or (remove_read_only and _has_read_only_properties(item))
                            or (remove_write_only and _has_write_only_properties(item))
                            or (convert_one_of_to_any_of and "oneOf" in item)
                            or _needs_recursive_processing(
                                item,
                                openapi_version,
                                remove_read_only,
                                remove_write_only,
                                convert_one_of_to_any_of,
                            )
                        )
                        if nested_needs_conversion:
                            return True
    return False


def _filter_properties_by_access(
    schema: dict[str, Any], remove_read_only: bool, remove_write_only: bool
) -> dict[str, Any]:
    """Remove readOnly and/or writeOnly properties from schema."""
    if "properties" not in schema:
        return schema

    result = schema.copy()
    filtered_properties = {}

    for prop_name, prop_schema in result["properties"].items():
        if not isinstance(prop_schema, dict):
            filtered_properties[prop_name] = prop_schema
            continue

        should_remove = (remove_read_only and prop_schema.get("readOnly")) or (
            remove_write_only and prop_schema.get("writeOnly")
        )

        if not should_remove:
            filtered_properties[prop_name] = prop_schema

    result["properties"] = filtered_properties

    # Clean up required array if properties were removed
    if "required" in result and filtered_properties:
        result["required"] = [
            prop for prop in result["required"] if prop in filtered_properties
        ]
        if not result["required"]:
            result.pop("required")

    return result


def convert_schema_definitions(
    schema_definitions: dict[str, Any] | None,
    openapi_version: str | None = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Convert a dictionary of OpenAPI schema definitions to JSON Schema.

    Args:
        schema_definitions: Dictionary of schema definitions
        openapi_version: OpenAPI version for optimization
        **kwargs: Additional arguments passed to convert_openapi_schema_to_json_schema

    Returns:
        Dictionary of converted schema definitions
    """
    if not schema_definitions:
        return {}

    return {
        name: convert_openapi_schema_to_json_schema(schema, openapi_version, **kwargs)
        for name, schema in schema_definitions.items()
    }
