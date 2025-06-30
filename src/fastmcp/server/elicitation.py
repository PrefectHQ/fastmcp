from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from mcp.server.elicitation import (
    CancelledElicitation,
    DeclinedElicitation,
)
from pydantic import BaseModel

from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

__all__ = [
    "AcceptedElicitation",
    "CancelledElicitation",
    "DeclinedElicitation",
    "get_elicitation_schema",
    "ScalarElicitationType",
]

logger = get_logger(__name__)

T = TypeVar("T")


# we can't use the low-level AcceptedElicitation because it only works with BaseModels
class AcceptedElicitation(BaseModel, Generic[T]):
    """Result when user accepts the elicitation."""

    action: Literal["accept"] = "accept"
    data: T


@dataclass
class ScalarElicitationType(Generic[T]):
    value: T


def get_elicitation_schema(response_type: type[T]) -> dict[str, Any]:
    """Get the schema for an elicitation response.

    Args:
        response_type: The type of the response
    """

    schema = get_cached_typeadapter(response_type).json_schema()
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

        # Check if it's a primitive type
        if prop_type not in ALLOWED_TYPES:
            raise TypeError(
                f"Elicitation schema field '{prop_name}' has type '{prop_type}' which is not "
                f"a primitive type. Only {ALLOWED_TYPES} are allowed in elicitation schemas."
            )

        # Check for nested objects or arrays of objects (not allowed)
        if prop_type == "object":
            raise TypeError(
                f"Elicitation schema field '{prop_name}' is an object, but nested objects are not allowed. "
                "Elicitation schemas must be flat objects with primitive properties only."
            )

        if prop_type == "array":
            items_schema = prop_schema.get("items", {})
            if items_schema.get("type") == "object":
                raise TypeError(
                    f"Elicitation schema field '{prop_name}' is an array of objects, but arrays of objects are not allowed. "
                    "Elicitation schemas must be flat objects with primitive properties only."
                )
