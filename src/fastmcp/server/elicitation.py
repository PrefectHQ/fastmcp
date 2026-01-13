from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Literal, cast, get_origin

from mcp.server.elicitation import (
    CancelledElicitation,
    DeclinedElicitation,
)
from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema
from typing_extensions import TypeVar

from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

__all__ = [
    "AcceptedElicitation",
    "CancelledElicitation",
    "DeclinedElicitation",
    "ElicitConfig",
    "ScalarElicitationType",
    "get_elicitation_schema",
    "handle_elicit_accept",
    "parse_elicit_response_type",
]

logger = get_logger(__name__)

T = TypeVar("T", default=Any)


class ElicitationJsonSchema(GenerateJsonSchema):
    """Custom JSON schema generator for MCP elicitation that always inlines enums.

    MCP elicitation requires inline enum schemas without $ref/$defs references.
    This generator ensures enums are always generated inline for compatibility.
    Optionally adds enumNames for better UI display when available.
    """

    def generate_inner(self, schema: core_schema.CoreSchema) -> JsonSchemaValue:  # type: ignore[override]
        """Override to prevent ref generation for enums and handle list schemas."""
        # For enum schemas, bypass the ref mechanism entirely
        if schema["type"] == "enum":
            # Directly call our custom enum_schema without going through handler
            # This prevents the ref/defs mechanism from being invoked
            return self.enum_schema(schema)
        # For list schemas, check if items are enums
        if schema["type"] == "list":
            return self.list_schema(schema)
        # For all other types, use the default implementation
        return super().generate_inner(schema)

    def list_schema(self, schema: core_schema.ListSchema) -> JsonSchemaValue:
        """Generate schema for list types, detecting enum items for multi-select."""
        items_schema = schema.get("items_schema")

        # Check if items are enum/Literal
        if items_schema and items_schema.get("type") == "enum":
            # Generate array with enum items
            items = self.enum_schema(items_schema)  # type: ignore[arg-type]
            # If items have oneOf pattern, convert to anyOf for multi-select per SEP-1330
            if "oneOf" in items:
                items = {"anyOf": items["oneOf"]}
            return {
                "type": "array",
                "items": items,  # Will be {"enum": [...]} or {"anyOf": [...]}
            }

        # Check if items are Literal (which Pydantic represents differently)
        if items_schema:
            # Try to detect Literal patterns
            items_result = super().generate_inner(items_schema)
            # If it's a const pattern or enum-like, allow it
            if (
                "const" in items_result
                or "enum" in items_result
                or "oneOf" in items_result
            ):
                # Convert oneOf to anyOf for multi-select
                if "oneOf" in items_result:
                    items_result = {"anyOf": items_result["oneOf"]}
                return {
                    "type": "array",
                    "items": items_result,
                }

        # Default behavior for non-enum arrays
        return super().list_schema(schema)

    def enum_schema(self, schema: core_schema.EnumSchema) -> JsonSchemaValue:
        """Generate inline enum schema.

        Always generates enum pattern: `{"enum": [value, ...]}`
        Titled enums are handled separately via dict-based syntax in ctx.elicit().
        """
        # Get the base schema from parent - always use simple enum pattern
        return super().enum_schema(schema)


# we can't use the low-level AcceptedElicitation because it only works with BaseModels
class AcceptedElicitation(BaseModel, Generic[T]):
    """Result when user accepts the elicitation."""

    action: Literal["accept"] = "accept"
    data: T


@dataclass
class ScalarElicitationType(Generic[T]):
    value: T


@dataclass
class ElicitConfig:
    """Configuration for an elicitation request.

    Attributes:
        schema: The JSON schema to send to the client
        response_type: The type to validate responses with (None for raw schemas)
        is_raw: True if schema was built directly (extract "value" from response)
    """

    schema: dict[str, Any]
    response_type: type | None
    is_raw: bool


def parse_elicit_response_type(response_type: Any) -> ElicitConfig:
    """Parse response_type into schema and handling configuration.

    Supports multiple syntaxes:
    - None: Empty object schema, expect empty response
    - dict: `{"low": {"title": "..."}}` -> single-select titled enum
    - list patterns:
        - `[["a", "b"]]` -> multi-select untitled
        - `[{"low": {...}}]` -> multi-select titled
        - `["a", "b"]` -> single-select untitled
    - `list[X]` type annotation: multi-select with type
    - Scalar types (bool, int, float, str, Literal, Enum): single value
    - Other types (dataclass, BaseModel): use directly
    """
    if response_type is None:
        return ElicitConfig(
            schema={"type": "object", "properties": {}},
            response_type=None,
            is_raw=False,
        )

    if isinstance(response_type, dict):
        return _parse_dict_syntax(response_type)

    if isinstance(response_type, list):
        return _parse_list_syntax(response_type)

    if get_origin(response_type) is list:
        return _parse_generic_list(response_type)

    if _is_scalar_type(response_type):
        return _parse_scalar_type(response_type)

    # Other types (dataclass, BaseModel, etc.) - use directly
    return ElicitConfig(
        schema=get_elicitation_schema(response_type),
        response_type=response_type,
        is_raw=False,
    )


def _is_scalar_type(response_type: Any) -> bool:
    """Check if response_type is a scalar type that needs wrapping."""
    return (
        response_type in {bool, int, float, str}
        or get_origin(response_type) is Literal
        or (isinstance(response_type, type) and issubclass(response_type, Enum))
    )


def _parse_dict_syntax(d: dict[str, Any]) -> ElicitConfig:
    """Parse dict syntax: {"low": {"title": "..."}} -> single-select titled."""
    if not d:
        raise ValueError("Dict response_type cannot be empty.")
    enum_schema = _dict_to_enum_schema(d, multi_select=False)
    return ElicitConfig(
        schema={
            "type": "object",
            "properties": {"value": enum_schema},
            "required": ["value"],
        },
        response_type=None,
        is_raw=True,
    )


def _parse_list_syntax(lst: list[Any]) -> ElicitConfig:
    """Parse list patterns: [[...]], [{...}], or [...]."""
    # [["a", "b", "c"]] -> multi-select untitled
    if (
        len(lst) == 1
        and isinstance(lst[0], list)
        and lst[0]
        and all(isinstance(item, str) for item in lst[0])
    ):
        return ElicitConfig(
            schema={
                "type": "object",
                "properties": {"value": {"type": "array", "items": {"enum": lst[0]}}},
                "required": ["value"],
            },
            response_type=None,
            is_raw=True,
        )

    # [{"low": {"title": "..."}}] -> multi-select titled
    if len(lst) == 1 and isinstance(lst[0], dict) and lst[0]:
        enum_schema = _dict_to_enum_schema(lst[0], multi_select=True)
        return ElicitConfig(
            schema={
                "type": "object",
                "properties": {"value": {"type": "array", "items": enum_schema}},
                "required": ["value"],
            },
            response_type=None,
            is_raw=True,
        )

    # ["a", "b", "c"] -> single-select untitled
    if lst and all(isinstance(item, str) for item in lst):
        # Construct Literal type from tuple - use cast since we can't construct Literal dynamically
        # but we know the values are all strings
        choice_literal: type[Any] = cast(type[Any], Literal[tuple(lst)])  # type: ignore[valid-type]
        wrapped = ScalarElicitationType[choice_literal]  # type: ignore[valid-type]
        return ElicitConfig(
            schema=get_elicitation_schema(wrapped),
            response_type=wrapped,
            is_raw=False,
        )

    raise ValueError(f"Invalid list response_type format. Received: {lst}")


def _parse_generic_list(response_type: Any) -> ElicitConfig:
    """Parse list[X] type annotation -> multi-select."""
    wrapped = ScalarElicitationType[response_type]
    return ElicitConfig(
        schema=get_elicitation_schema(wrapped),
        response_type=wrapped,
        is_raw=False,
    )


def _parse_scalar_type(response_type: Any) -> ElicitConfig:
    """Parse scalar types (bool, int, float, str, Literal, Enum)."""
    wrapped = ScalarElicitationType[response_type]
    return ElicitConfig(
        schema=get_elicitation_schema(wrapped),
        response_type=wrapped,
        is_raw=False,
    )


def handle_elicit_accept(
    config: ElicitConfig, content: Any
) -> AcceptedElicitation[Any]:
    """Handle an accepted elicitation response.

    Args:
        config: The elicitation configuration from parse_elicit_response_type
        content: The response content from the client

    Returns:
        AcceptedElicitation with the extracted/validated data
    """
    # For raw schemas (dict/nested-list syntax), extract value directly
    if config.is_raw:
        if not isinstance(content, dict) or "value" not in content:
            raise ValueError("Elicitation response missing required 'value' field.")
        return AcceptedElicitation[Any](data=content["value"])

    # For typed schemas, validate with Pydantic
    if config.response_type is not None:
        type_adapter = get_cached_typeadapter(config.response_type)
        validated_data = type_adapter.validate_python(content)
        if isinstance(validated_data, ScalarElicitationType):
            return AcceptedElicitation[Any](data=validated_data.value)
        return AcceptedElicitation[Any](data=validated_data)

    # For None response_type, expect empty response
    if content:
        raise ValueError(
            f"Elicitation expected an empty response, but received: {content}"
        )
    return AcceptedElicitation[dict[str, Any]](data={})


def _dict_to_enum_schema(
    enum_dict: dict[str, dict[str, str]], multi_select: bool = False
) -> dict[str, Any]:
    """Convert dict enum to SEP-1330 compliant schema pattern.

    Args:
        enum_dict: {"low": {"title": "Low Priority"}, "medium": {"title": "Medium Priority"}}
        multi_select: If True, use anyOf pattern; if False, use oneOf pattern

    Returns:
        {"type": "string", "oneOf": [...]} for single-select
        {"anyOf": [...]} for multi-select (used as array items)
    """
    pattern_key = "anyOf" if multi_select else "oneOf"
    pattern = []
    for value, metadata in enum_dict.items():
        title = metadata.get("title", value)
        pattern.append({"const": value, "title": title})

    result: dict[str, Any] = {pattern_key: pattern}
    if not multi_select:
        result["type"] = "string"
    return result


def get_elicitation_schema(response_type: type[T]) -> dict[str, Any]:
    """Get the schema for an elicitation response.

    Args:
        response_type: The type of the response
    """

    # Use custom schema generator that inlines enums for MCP compatibility
    schema = get_cached_typeadapter(response_type).json_schema(
        schema_generator=ElicitationJsonSchema
    )
    schema = compress_schema(schema)

    # Validate the schema to ensure it follows MCP elicitation requirements
    validate_elicitation_json_schema(schema)

    return schema


def validate_elicitation_json_schema(schema: dict[str, Any]) -> None:
    """Validate that a JSON schema follows MCP elicitation requirements.

    This ensures the schema is compatible with MCP elicitation requirements:
    - Must be an object schema
    - Must only contain primitive field types (string, number, integer, boolean)
    - Must be flat (no nested objects or arrays of objects)
    - Allows const fields (for Literal types) and enum fields (for Enum types)
    - Only primitive types and their nullable variants are allowed

    Args:
        schema: The JSON schema to validate

    Raises:
        TypeError: If the schema doesn't meet MCP elicitation requirements
    """
    ALLOWED_TYPES = {"string", "number", "integer", "boolean"}

    # Check that the schema is an object
    if schema.get("type") != "object":
        raise TypeError(
            f"Elicitation schema must be an object schema, got type '{schema.get('type')}'. "
            "Elicitation schemas are limited to flat objects with primitive properties only."
        )

    properties = schema.get("properties", {})

    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type")

        # Handle nullable types
        if isinstance(prop_type, list):
            if "null" in prop_type:
                prop_type = [t for t in prop_type if t != "null"]
                if len(prop_type) == 1:
                    prop_type = prop_type[0]
        elif prop_schema.get("nullable", False):
            continue  # Nullable with no other type is fine

        # Handle const fields (Literal types)
        if "const" in prop_schema:
            continue  # const fields are allowed regardless of type

        # Handle enum fields (Enum types)
        if "enum" in prop_schema:
            continue  # enum fields are allowed regardless of type

        # Handle references to definitions (like Enum types)
        if "$ref" in prop_schema:
            # Get the referenced definition
            ref_path = prop_schema["$ref"]
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path[8:]  # Remove "#/$defs/" prefix
                ref_def = schema.get("$defs", {}).get(def_name, {})
                # If the referenced definition has an enum, it's allowed
                if "enum" in ref_def:
                    continue
                # If the referenced definition has a type that's allowed, it's allowed
                ref_type = ref_def.get("type")
                if ref_type in ALLOWED_TYPES:
                    continue
            # If we can't determine what the ref points to, reject it for safety
            raise TypeError(
                f"Elicitation schema field '{prop_name}' contains a reference '{ref_path}' "
                "that could not be validated. Only references to enum types or primitive types are allowed."
            )

        # Handle union types (oneOf/anyOf)
        if "oneOf" in prop_schema or "anyOf" in prop_schema:
            union_schemas = prop_schema.get("oneOf", []) + prop_schema.get("anyOf", [])
            for union_schema in union_schemas:
                # Allow const and enum in unions
                if "const" in union_schema or "enum" in union_schema:
                    continue
                union_type = union_schema.get("type")
                if union_type not in ALLOWED_TYPES:
                    raise TypeError(
                        f"Elicitation schema field '{prop_name}' has union type '{union_type}' which is not "
                        f"a primitive type. Only {ALLOWED_TYPES} are allowed in elicitation schemas."
                    )
            continue

        # Check for arrays before checking primitive types
        if prop_type == "array":
            items_schema = prop_schema.get("items", {})
            if items_schema.get("type") == "object":
                raise TypeError(
                    f"Elicitation schema field '{prop_name}' is an array of objects, but arrays of objects are not allowed. "
                    "Elicitation schemas must be flat objects with primitive properties only."
                )

            # Allow arrays with enum patterns (for multi-select)
            if "enum" in items_schema:
                continue  # Allowed: {"type": "array", "items": {"enum": [...]}}

            # Allow arrays with oneOf/anyOf const patterns (SEP-1330)
            if "oneOf" in items_schema or "anyOf" in items_schema:
                union_schemas = items_schema.get("oneOf", []) + items_schema.get(
                    "anyOf", []
                )
                if union_schemas and all("const" in s for s in union_schemas):
                    continue  # Allowed: {"type": "array", "items": {"anyOf": [{"const": ...}, ...]}}

            # Reject other array types (e.g., arrays of primitives without enum pattern)
            raise TypeError(
                f"Elicitation schema field '{prop_name}' is an array, but arrays are only allowed "
                "when items are enums (for multi-select). Only enum arrays are supported in elicitation schemas."
            )

        # Check for nested objects (not allowed)
        if prop_type == "object":
            raise TypeError(
                f"Elicitation schema field '{prop_name}' is an object, but nested objects are not allowed. "
                "Elicitation schemas must be flat objects with primitive properties only."
            )

        # Check if it's a primitive type
        if prop_type not in ALLOWED_TYPES:
            raise TypeError(
                f"Elicitation schema field '{prop_name}' has type '{prop_type}' which is not "
                f"a primitive type. Only {ALLOWED_TYPES} are allowed in elicitation schemas."
            )
