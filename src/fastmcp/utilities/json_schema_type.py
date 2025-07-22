"""Convert JSON Schema to Python types with validation.

The json_schema_to_type function converts a JSON Schema into a Python type that can be used
for validation with Pydantic. It supports:

- Basic types (string, number, integer, boolean, null)
- Complex types (arrays, objects)
- Format constraints (date-time, email, uri)
- Numeric constraints (minimum, maximum, multipleOf)
- String constraints (minLength, maxLength, pattern)
- Array constraints (minItems, maxItems, uniqueItems)
- Object properties with defaults
- References and recursive schemas
- Enums and constants
- Union types

Example:
    ```python
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "age": {"type": "integer", "minimum": 0},
            "email": {"type": "string", "format": "email"}
        },
        "required": ["name", "age"]
    }

    # Name is optional and will be inferred from schema's "title" property if not provided
    Person = json_schema_to_type(schema)
    # Creates a validated dataclass with name, age, and optional email fields
    ```
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import MISSING, field, make_dataclass
from datetime import datetime
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Literal,
    Union,
)

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    Json,
    StringConstraints,
    model_validator,
)
from typing_extensions import NotRequired, TypedDict

__all__ = ["json_schema_to_type", "JSONSchema"]


FORMAT_TYPES: dict[str, Any] = {
    "date-time": datetime,
    "email": EmailStr,
    "uri": AnyUrl,
    "json": Json,
}

_classes: dict[tuple[str, Any], type | None] = {}


class JSONSchema(TypedDict):
    type: NotRequired[str | list[str]]
    properties: NotRequired[dict[str, JSONSchema]]
    required: NotRequired[list[str]]
    additionalProperties: NotRequired[bool | JSONSchema]
    items: NotRequired[JSONSchema | list[JSONSchema]]
    enum: NotRequired[list[Any]]
    const: NotRequired[Any]
    default: NotRequired[Any]
    description: NotRequired[str]
    title: NotRequired[str]
    examples: NotRequired[list[Any]]
    format: NotRequired[str]
    allOf: NotRequired[list[JSONSchema]]
    anyOf: NotRequired[list[JSONSchema]]
    oneOf: NotRequired[list[JSONSchema]]
    not_: NotRequired[JSONSchema]
    definitions: NotRequired[dict[str, JSONSchema]]
    dependencies: NotRequired[dict[str, JSONSchema | list[str]]]
    pattern: NotRequired[str]
    minLength: NotRequired[int]
    maxLength: NotRequired[int]
    minimum: NotRequired[int | float]
    maximum: NotRequired[int | float]
    exclusiveMinimum: NotRequired[int | float]
    exclusiveMaximum: NotRequired[int | float]
    multipleOf: NotRequired[int | float]
    uniqueItems: NotRequired[bool]
    minItems: NotRequired[int]
    maxItems: NotRequired[int]
    additionalItems: NotRequired[bool | JSONSchema]


def json_schema_to_type(
    schema: Mapping[str, Any],
    name: str | None = None,
) -> type:
    """Convert JSON schema to appropriate Python type with validation.

    Args:
        schema: A JSON Schema dictionary defining the type structure and validation rules
        name: Optional name for object schemas. Only allowed when schema type is "object".
            If not provided for objects, name will be inferred from schema's "title"
            property or default to "Root".

    Returns:
        A Python type (typically a dataclass for objects) with Pydantic validation

    Raises:
        ValueError: If a name is provided for a non-object schema

    Examples:
        Create a dataclass from an object schema:
        ```python
        schema = {
            "type": "object",
            "title": "Person",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "age": {"type": "integer", "minimum": 0},
                "email": {"type": "string", "format": "email"}
            },
            "required": ["name", "age"]
        }

        Person = json_schema_to_type(schema)
        # Creates a dataclass with name, age, and optional email fields:
        # @dataclass
        # class Person:
        #     name: str
        #     age: int
        #     email: str | None = None
        ```
        Person(name="John", age=30)

        Create a scalar type with constraints:
        ```python
        schema = {
            "type": "string",
            "minLength": 3,
            "pattern": "^[A-Z][a-z]+$"
        }

        NameType = json_schema_to_type(schema)
        # Creates Annotated[str, StringConstraints(min_length=3, pattern="^[A-Z][a-z]+$")]

        @dataclass
        class Name:
            name: NameType
        ```
    """
    # Always use the top-level schema for references
    if schema.get("type") == "object":
        # If no properties defined but has additionalProperties, return typed dict
        if not schema.get("properties") and schema.get("additionalProperties"):
            additional_props = schema["additionalProperties"]
            if additional_props is True:
                return dict[str, Any]  # type: ignore - additionalProperties: true means dict[str, Any]
            else:
                # Handle typed dictionaries like dict[str, str]
                value_type = _schema_to_type(additional_props, schemas=schema)
                return dict[str, value_type]  # type: ignore
        # If no properties and no additionalProperties, default to dict[str, Any] for safety
        elif not schema.get("properties") and not schema.get("additionalProperties"):
            return dict[str, Any]  # type: ignore
        # If has properties AND additionalProperties is True, use Pydantic BaseModel
        elif schema.get("properties") and schema.get("additionalProperties") is True:
            return _create_pydantic_model(schema, name, schemas=schema)
        # Otherwise use fast dataclass
        return _create_dataclass(schema, name, schemas=schema)
    elif name:
        raise ValueError(f"Can not apply name to non-object schema: {name}")
    result = _schema_to_type(schema, schemas=schema)
    return result  # type: ignore[return-value]


def _hash_schema(schema: Mapping[str, Any]) -> str:
    """Generate a deterministic hash for schema caching."""
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _resolve_ref(ref: str, schemas: Mapping[str, Any]) -> Mapping[str, Any]:
    """Resolve JSON Schema reference to target schema."""
    path = ref.replace("#/", "").split("/")
    current = schemas
    for part in path:
        current = current.get(part, {})
    return current


def _create_string_type(schema: Mapping[str, Any]) -> type | Annotated[Any, ...]:
    """Create string type with optional constraints."""
    if "const" in schema:
        return Literal[schema["const"]]  # type: ignore

    if fmt := schema.get("format"):
        if fmt == "uri":
            return AnyUrl
        elif fmt == "uri-reference":
            return str
        return FORMAT_TYPES.get(fmt, str)

    constraints = {
        k: v
        for k, v in {
            "min_length": schema.get("minLength"),
            "max_length": schema.get("maxLength"),
            "pattern": schema.get("pattern"),
        }.items()
        if v is not None
    }

    return Annotated[str, StringConstraints(**constraints)] if constraints else str


def _create_numeric_type(
    base: type[int | float], schema: Mapping[str, Any]
) -> type | Annotated[Any, ...]:
    """Create numeric type with optional constraints."""
    if "const" in schema:
        return Literal[schema["const"]]  # type: ignore

    constraints = {
        k: v
        for k, v in {
            "gt": schema.get("exclusiveMinimum"),
            "ge": schema.get("minimum"),
            "lt": schema.get("exclusiveMaximum"),
            "le": schema.get("maximum"),
            "multiple_of": schema.get("multipleOf"),
        }.items()
        if v is not None
    }

    return Annotated[base, Field(**constraints)] if constraints else base


def _create_enum(name: str, values: list[Any]) -> type:
    """Create enum type from list of values."""
    # Always return Literal for enum fields to preserve the literal nature
    return Literal[tuple(values)]  # type: ignore[return-value]


def _create_array_type(
    schema: Mapping[str, Any], schemas: Mapping[str, Any]
) -> type | Annotated[Any, ...]:
    """Create list/set type with optional constraints."""
    items = schema.get("items", {})
    if isinstance(items, list):
        # Handle positional item schemas
        item_types = [_schema_to_type(s, schemas) for s in items]
        combined = Union[tuple(item_types)]  # type: ignore # noqa: UP007
        base = list[combined]
    else:
        # Handle single item schema
        item_type = _schema_to_type(items, schemas)
        base_class = set if schema.get("uniqueItems") else list
        base = base_class[item_type]  # type: ignore[misc]

    constraints = {
        k: v
        for k, v in {
            "min_length": schema.get("minItems"),
            "max_length": schema.get("maxItems"),
        }.items()
        if v is not None
    }

    return Annotated[base, Field(**constraints)] if constraints else base


def _return_Any() -> Any:
    return Any


def _get_from_type_handler(
    schema: Mapping[str, Any], schemas: Mapping[str, Any]
) -> Callable[..., Any]:
    """Get the appropriate type handler for the schema."""

    type_handlers: dict[str, Callable[..., Any]] = {  # TODO
        "string": lambda s: _create_string_type(s),  # type: ignore
        "integer": lambda s: _create_numeric_type(int, s),  # type: ignore
        "number": lambda s: _create_numeric_type(float, s),  # type: ignore
        "boolean": lambda _: bool,  # type: ignore
        "null": lambda _: type(None),  # type: ignore
        "array": lambda s: _create_array_type(s, schemas),  # type: ignore
        "object": lambda s: (
            _create_pydantic_model(s, s.get("title"), schemas)
            if s.get("properties") and s.get("additionalProperties") is True
            else _create_dataclass(s, s.get("title"), schemas)
        ),  # type: ignore
    }
    return type_handlers.get(schema.get("type", None), _return_Any)


def _schema_to_type(
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
) -> type | ForwardRef:
    """Convert schema to appropriate Python type."""
    if not schema:
        return object

    if "type" not in schema and "properties" in schema:
        return _create_dataclass(schema, schema.get("title", "<unknown>"), schemas)

    # Handle references first
    if "$ref" in schema:
        ref = schema["$ref"]
        # Handle self-reference
        if ref == "#":
            return ForwardRef(schema.get("title", "Root"))  # type: ignore[return-value]
        return _schema_to_type(_resolve_ref(ref, schemas), schemas)

    if "const" in schema:
        return Literal[schema["const"]]  # type: ignore

    if "enum" in schema:
        return _create_enum(f"Enum_{len(_classes)}", schema["enum"])

    # Handle anyOf unions
    if "anyOf" in schema:
        types: list[type | Any] = []
        for subschema in schema["anyOf"]:
            # Special handling for dict-like objects in unions
            if (
                subschema.get("type") == "object"
                and not subschema.get("properties")
                and subschema.get("additionalProperties")
            ):
                # This is a dict type, handle it directly
                additional_props = subschema["additionalProperties"]
                if additional_props is True:
                    types.append(dict[str, Any])  # type: ignore
                else:
                    value_type = _schema_to_type(additional_props, schemas)
                    types.append(dict[str, value_type])  # type: ignore
            else:
                types.append(_schema_to_type(subschema, schemas))

        # Check if one of the types is None (null)
        has_null = type(None) in types
        types = [t for t in types if t is not type(None)]

        if len(types) == 0:
            return type(None)
        elif len(types) == 1:
            if has_null:
                return types[0] | None  # type: ignore
            else:
                return types[0]
        else:
            if has_null:
                return Union[tuple(types + [type(None)])]  # type: ignore # noqa: UP007
            else:
                return Union[tuple(types)]  # type: ignore # noqa: UP007

    schema_type = schema.get("type")
    if not schema_type:
        return Any  # type: ignore[return-value]

    if isinstance(schema_type, list):
        # Create a copy of the schema for each type, but keep all constraints
        types: list[type | Any] = []
        for t in schema_type:
            type_schema = dict(schema)
            type_schema["type"] = t
            types.append(_schema_to_type(type_schema, schemas))
        has_null = type(None) in types
        types = [t for t in types if t is not type(None)]
        if has_null:
            if len(types) == 1:
                return types[0] | None  # type: ignore
            else:
                return Union[tuple(types + [type(None)])]  # type: ignore # noqa: UP007
        return Union[tuple(types)]  # type: ignore # noqa: UP007

    return _get_from_type_handler(schema, schemas)(schema)


def _sanitize_name(name: str) -> str:
    """Convert string to valid Python identifier."""
    original_name = name
    # Step 1: replace everything except [0-9a-zA-Z_] with underscores
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    # Step 2: deduplicate underscores
    cleaned = re.sub(r"__+", "_", cleaned)
    # Step 3: if the first char of original name isn't a letter or underscore, prepend field_
    if not name or not re.match(r"[a-zA-Z_]", name[0]):
        cleaned = f"field_{cleaned}"
    # Step 4: deduplicate again
    cleaned = re.sub(r"__+", "_", cleaned)
    # Step 5: only strip trailing underscores if they weren't in the original name
    if not original_name.endswith("_"):
        cleaned = cleaned.rstrip("_")
    return cleaned


def _get_default_value(
    schema: dict[str, Any],
    prop_name: str,
    parent_default: dict[str, Any] | None = None,
) -> Any:
    """Get default value with proper priority ordering.
    1. Value from parent's default if it exists
    2. Property's own default if it exists
    3. None
    """
    if parent_default is not None and prop_name in parent_default:
        return parent_default[prop_name]
    return schema.get("default")


def _create_field_with_default(
    field_type: type,
    default_value: Any,
    schema: dict[str, Any],
) -> Any:
    """Create a field with simplified default handling."""
    # Always use None as default for complex types
    if isinstance(default_value, dict | list) or default_value is None:
        return field(default=None)

    # For simple types, use the value directly
    return field(default=default_value)


def _create_pydantic_model(
    schema: Mapping[str, Any],
    name: str | None = None,
    schemas: Mapping[str, Any] | None = None,
) -> type:
    """Create Pydantic BaseModel from object schema with additionalProperties."""
    name = name or schema.get("title", "Root")
    assert name is not None  # Should not be None after the or operation
    sanitized_name = _sanitize_name(name)
    schema_hash = _hash_schema(schema)
    cache_key = (schema_hash, sanitized_name)

    # Return existing class if already built
    if cache_key in _classes:
        existing = _classes[cache_key]
        if existing is None:
            return ForwardRef(sanitized_name)  # type: ignore[return-value]
        return existing

    # Place placeholder for recursive references
    _classes[cache_key] = None

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Build field annotations and defaults
    annotations = {}
    defaults = {}

    for prop_name, prop_schema in properties.items():
        field_type = _schema_to_type(prop_schema, schemas or {})

        # Handle defaults
        default_value = prop_schema.get("default", MISSING)
        if default_value is not MISSING:
            defaults[prop_name] = default_value
            annotations[prop_name] = field_type
        elif prop_name in required:
            annotations[prop_name] = field_type
        else:
            annotations[prop_name] = Union[field_type, type(None)]  # type: ignore[misc]  # noqa: UP007
            defaults[prop_name] = None

    # Create Pydantic model class
    cls_dict = {
        "__annotations__": annotations,
        "model_config": ConfigDict(extra="allow"),
        **defaults,
    }

    cls = type(sanitized_name, (BaseModel,), cls_dict)

    # Store completed class
    _classes[cache_key] = cls
    return cls


def _create_dataclass(
    schema: Mapping[str, Any],
    name: str | None = None,
    schemas: Mapping[str, Any] | None = None,
) -> type:
    """Create dataclass from object schema."""
    name = name or schema.get("title", "Root")
    # Sanitize name for class creation
    assert name is not None  # Should not be None after the or operation
    sanitized_name = _sanitize_name(name)
    schema_hash = _hash_schema(schema)
    cache_key = (schema_hash, sanitized_name)
    original_schema = dict(schema)  # Store copy for validator

    # Return existing class if already built
    if cache_key in _classes:
        existing = _classes[cache_key]
        if existing is None:
            return ForwardRef(sanitized_name)  # type: ignore[return-value]
        return existing

    # Place placeholder for recursive references
    _classes[cache_key] = None

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref == "#":
            return ForwardRef(sanitized_name)  # type: ignore[return-value]
        schema = _resolve_ref(ref, schemas or {})

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    fields: list[tuple[Any, ...]] = []
    for prop_name, prop_schema in properties.items():
        field_name = _sanitize_name(prop_name)

        # Check for self-reference in property
        if prop_schema.get("$ref") == "#":
            field_type = ForwardRef(sanitized_name)
        else:
            field_type = _schema_to_type(prop_schema, schemas or {})

        default_val = prop_schema.get("default", MISSING)
        is_required = prop_name in required

        # Include alias in field metadata
        meta = {"alias": prop_name}

        if default_val is not MISSING:
            if isinstance(default_val, dict | list):
                field_def = field(
                    default_factory=lambda d=default_val: deepcopy(d), metadata=meta
                )
            else:
                field_def = field(default=default_val, metadata=meta)
        else:
            if is_required:
                field_def = field(metadata=meta)
            else:
                field_def = field(default=None, metadata=meta)

        if is_required or default_val is not MISSING:
            fields.append((field_name, field_type, field_def))
        else:
            fields.append((field_name, Union[field_type, type(None)], field_def))  # type: ignore[misc]  # noqa: UP007

    cls = make_dataclass(sanitized_name, fields, kw_only=True)

    # Add model validator for defaults
    @model_validator(mode="before")
    @classmethod
    def _apply_defaults(cls, data: Mapping[str, Any]):
        if isinstance(data, dict):
            return _merge_defaults(data, original_schema)
        return data

    setattr(cls, "_apply_defaults", _apply_defaults)

    # Store completed class
    _classes[cache_key] = cls
    return cls


def _merge_defaults(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    parent_default: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge defaults with provided data at all levels."""
    # If we have no data
    if not data:
        # Start with parent default if available
        if parent_default:
            result = dict(parent_default)
        # Otherwise use schema default if available
        elif "default" in schema:
            result = dict(schema["default"])
        # Otherwise start empty
        else:
            result = {}
    # If we have data and a parent default, merge them
    elif parent_default:
        result = dict(parent_default)
        for key, value in data.items():
            if (
                isinstance(value, dict)
                and key in result
                and isinstance(result[key], dict)
            ):
                # recursively merge nested dicts
                result[key] = _merge_defaults(value, {"properties": {}}, result[key])
            else:
                result[key] = value
    # Otherwise just use the data
    else:
        result = dict(data)

    # For each property in the schema
    for prop_name, prop_schema in schema.get("properties", {}).items():
        # If property is missing, apply defaults in priority order
        if prop_name not in result:
            if parent_default and prop_name in parent_default:
                result[prop_name] = parent_default[prop_name]
            elif "default" in prop_schema:
                result[prop_name] = prop_schema["default"]

        # If property exists and is an object, recursively merge
        if (
            prop_name in result
            and isinstance(result[prop_name], dict)
            and prop_schema.get("type") == "object"
        ):
            # Get the appropriate default for this nested object
            nested_default = None
            if parent_default and prop_name in parent_default:
                nested_default = parent_default[prop_name]
            elif "default" in prop_schema:
                nested_default = prop_schema["default"]

            result[prop_name] = _merge_defaults(
                result[prop_name], prop_schema, nested_default
            )

    return result
