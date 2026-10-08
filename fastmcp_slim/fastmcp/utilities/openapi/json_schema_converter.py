"""
Clean OpenAPI 3.0 to JSON Schema converter for the experimental parser.

This module provides a systematic approach to converting OpenAPI 3.0 schemas
to JSON Schema, inspired by py-openapi-schema-to-json-schema but optimized
for our specific use case.
"""

from collections.abc import Iterator
from typing import Any

from fastmcp.utilities.json_schema import require_discriminator_property
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
    "$defs": dict,
    "$definitions": dict,
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
    definitions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert an OpenAPI schema to JSON Schema format.

    This is a clean, systematic approach that:
    1. Removes OpenAPI-specific fields
    2. Converts nullable fields to type arrays (for OpenAPI 3.0 only)
    3. Converts oneOf to anyOf for overlapping union handling
    4. Recursively processes nested schemas
    5. Optionally removes readOnly/writeOnly properties (see
       ``_filter_properties_by_access`` for the exact semantics)

    Args:
        schema: OpenAPI schema dictionary
        openapi_version: OpenAPI version for optimization
        remove_read_only: Whether to remove readOnly properties
        remove_write_only: Whether to remove writeOnly properties
        convert_one_of_to_any_of: Whether to convert oneOf to anyOf
        definitions: Unconverted schemas that local ``#/$defs/...`` references
            resolve against when deciding which properties to remove. Defaults
            to the schema's own ``$defs``.

    Returns:
        JSON Schema-compatible dictionary
    """
    if not isinstance(schema, dict):
        return schema

    if definitions is None:
        own_defs = schema.get("$defs")
        definitions = own_defs if isinstance(own_defs, dict) else None

    # Early exit optimization - check if conversion is needed
    needs_conversion = (
        any(field in schema for field in OPENAPI_SPECIFIC_FIELDS)
        or (remove_read_only and _has_read_only_properties(schema))
        or (remove_write_only and _has_write_only_properties(schema))
        or (convert_one_of_to_any_of and "oneOf" in schema)
        # A property can be restricted through a $ref that nothing local reveals
        or (bool(definitions) and (remove_read_only or remove_write_only))
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

    # Step 0: Project readOnly/writeOnly while the annotations still exist; the
    # steps below (and the conversion of nested schemas) discard them
    if remove_read_only or remove_write_only:
        result = _filter_properties_by_access(
            result, remove_read_only, remove_write_only, definitions
        )

    # Step 1: Handle nullable field conversion (OpenAPI 3.0 only)
    if openapi_version and openapi_version.startswith("3.0"):
        result = _convert_nullable_field(result)

    # Step 2: Convert oneOf to anyOf if requested
    if convert_one_of_to_any_of and "oneOf" in result:
        result["anyOf"] = result.pop("oneOf")

    # Step 3: Preserve discriminator tag presence before removing the keyword
    result = require_discriminator_property(result)

    # Step 4: Remove OpenAPI-specific fields
    for field in OPENAPI_SPECIFIC_FIELDS:
        result.pop(field, None)

    # Step 5: Recursively process nested schemas
    for field_name, field_type in RECURSIVE_FIELDS.items():
        if field_name in result:
            if field_type is dict and isinstance(result[field_name], dict):
                if field_name in ("properties", "$defs", "$definitions"):
                    # Handle maps of schemas (properties, $defs, $definitions)
                    result[field_name] = {
                        name: convert_openapi_schema_to_json_schema(
                            sub_schema,
                            openapi_version,
                            remove_read_only,
                            remove_write_only,
                            convert_one_of_to_any_of,
                            definitions,
                        )
                        if isinstance(sub_schema, dict)
                        else sub_schema
                        for name, sub_schema in result[field_name].items()
                    }
                else:
                    result[field_name] = convert_openapi_schema_to_json_schema(
                        result[field_name],
                        openapi_version,
                        remove_read_only,
                        remove_write_only,
                        convert_one_of_to_any_of,
                        definitions,
                    )
            elif field_type is list and isinstance(result[field_name], list):
                result[field_name] = [
                    convert_openapi_schema_to_json_schema(
                        item,
                        openapi_version,
                        remove_read_only,
                        remove_write_only,
                        convert_one_of_to_any_of,
                        definitions,
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
            result["anyOf"] = [*result["anyOf"], {"type": "null"}]
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
                if field_name in ("properties", "$defs", "$definitions"):
                    # Check if any schema in the map needs conversion
                    for sub_schema in schema[field_name].values():
                        if isinstance(sub_schema, dict):
                            nested_needs_conversion = (
                                any(
                                    field in sub_schema
                                    for field in OPENAPI_SPECIFIC_FIELDS
                                )
                                or (remove_read_only and sub_schema.get("readOnly"))
                                or (remove_write_only and sub_schema.get("writeOnly"))
                                or (convert_one_of_to_any_of and "oneOf" in sub_schema)
                                or _needs_recursive_processing(
                                    sub_schema,
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


def _scope_schemas(
    schema: dict[str, Any],
    definitions: dict[str, Any] | None,
    seen_refs: frozenset[str] = frozenset(),
) -> Iterator[dict[str, Any]]:
    """Yield ``schema`` and the schemas describing the same instance through a
    local ``$ref`` or an ``allOf`` member (reference cycles are skipped)."""
    yield schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/") and ref not in seen_refs:
        target = (definitions or {}).get(ref[len("#/$defs/") :])
        if isinstance(target, dict):
            yield from _scope_schemas(target, definitions, seen_refs | {ref})
    all_of = schema.get("allOf")
    for member in all_of if isinstance(all_of, list) else []:
        if isinstance(member, dict):
            yield from _scope_schemas(member, definitions, seen_refs)


def _access_restricted_names(
    schema: dict[str, Any],
    access_keys: tuple[str, ...],
    definitions: dict[str, Any] | None,
) -> set[str]:
    """Names of the object's properties annotated with any of ``access_keys``.

    A property counts when its own schema, its ``$ref`` target, or one of its
    ``allOf`` members carries the annotation. The object is what ``schema``
    describes together with its ``$ref``/``allOf`` members; nested objects and
    array items are separate scopes and are not inspected.
    """
    names: set[str] = set()
    for scope in _scope_schemas(schema, definitions):
        properties = scope.get("properties")
        if not isinstance(properties, dict):
            continue
        for name, prop in properties.items():
            if isinstance(prop, dict) and any(
                member.get(key)
                for member in _scope_schemas(prop, definitions)
                for key in access_keys
            ):
                names.add(name)
    return names


def _drop_property_names(schema: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """Remove ``names`` from ``properties`` and ``required``, including in inline
    ``allOf``/``anyOf``/``oneOf`` members, which constrain the same instance."""
    result = schema.copy()
    if isinstance(properties := result.get("properties"), dict):
        result["properties"] = {k: v for k, v in properties.items() if k not in names}
    if isinstance(required := result.get("required"), list):
        remaining = [name for name in required if name not in names]
        if remaining:
            result["required"] = remaining
        else:
            result.pop("required")
    for key in ("allOf", "anyOf", "oneOf"):
        if isinstance(members := result.get(key), list):
            result[key] = [
                _drop_property_names(member, names)
                if isinstance(member, dict)
                else member
                for member in members
            ]
    return result


def _filter_properties_by_access(
    schema: dict[str, Any],
    remove_read_only: bool,
    remove_write_only: bool,
    definitions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove readOnly and/or writeOnly properties from an object schema.

    Must run before the annotations are discarded. The excluded names are
    collected for the whole object (the schema plus its ``$ref`` and ``allOf``
    members, see ``_access_restricted_names``) and exactly those names are then
    dropped from ``properties`` and ``required`` wherever the object declares or
    requires them. Other requirements are kept, even when the property is
    defined by an ``allOf`` member or covered by ``additionalProperties``.
    """
    access_keys: tuple[str, ...] = ()
    if remove_read_only:
        access_keys += ("readOnly",)
    if remove_write_only:
        access_keys += ("writeOnly",)
    excluded = _access_restricted_names(schema, access_keys, definitions)
    if not excluded:
        return schema
    return _drop_property_names(schema, excluded)


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
