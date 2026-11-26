from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Literal, get_origin

from mcp.server.elicitation import (
    CancelledElicitation,
    DeclinedElicitation,
)
from pydantic import AliasChoices, AliasPath, BaseModel, ValidationError
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Enum definitions shared by several fields, keyed by their core ref
        self._enum_definitions: dict[str, core_schema.EnumSchema] = {}

    def definitions_schema(
        self, schema: core_schema.DefinitionsSchema
    ) -> JsonSchemaValue:
        """Record shared enum definitions so references to them can be inlined."""
        for definition in schema["definitions"]:
            if definition["type"] == "enum":
                self._enum_definitions[definition["ref"]] = definition
        return super().definitions_schema(schema)

    def definition_ref_schema(
        self, schema: core_schema.DefinitionReferenceSchema
    ) -> JsonSchemaValue:
        """Inline references to shared enums; enum definitions are never emitted."""
        enum_definition = self._enum_definitions.get(schema["schema_ref"])
        if enum_definition is not None:
            return self.enum_schema(enum_definition)
        return super().definition_ref_schema(schema)

    def generate_inner(self, schema: core_schema.CoreSchema) -> JsonSchemaValue:  # type: ignore[override]  # ty:ignore[incompatible-method-override]
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
            items = self.enum_schema(items_schema)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
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

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        """Offer an optional `T | None` field as a plain `T` property.

        MCP elicitation properties have no null type. A field with a default is
        left out of `required`, so the client can express "no value" by omitting
        it. A required `T | None` field keeps its null type and is rejected by
        schema validation, since narrowing it to `T` would change its contract.
        """
        inner = schema["schema"]
        if inner["type"] == "nullable":
            schema = {**schema, "schema": inner["schema"]}
        json_schema = super().default_schema(schema)
        # A `None` default has no meaning once the null type is gone
        if json_schema.get("default") is None:
            json_schema.pop("default", None)
        elif (
            json_schema.get("type") == "string"
            and "enum" in json_schema
            and isinstance(json_schema.get("default"), int | float)
            and not isinstance(json_schema.get("default"), bool)
        ):
            # A numeric choice rendered as a string enum carries its default
            # in the same string form.
            json_schema["default"] = str(json_schema["default"])
        elif (
            json_schema.get("type") == "array"
            and isinstance(json_schema.get("items"), dict)
            and isinstance(json_schema["items"].get("enum"), list)
            and isinstance(json_schema.get("default"), list)
        ):
            # A multi-select default lists the same stringified choices as
            # the items enum.
            items_enum = json_schema["items"]["enum"]
            json_schema["default"] = [
                str(value)
                if isinstance(value, int | float)
                and not isinstance(value, bool)
                and str(value) in items_enum
                else value
                for value in json_schema["default"]
            ]
        return json_schema

    @staticmethod
    def _stringify_numeric_enum(json_schema: JsonSchemaValue) -> JsonSchemaValue:
        """Render numeric `enum`/`const` choices as MCP string enums.

        MCP 2025-11-25 enums are string-only, so an integer or float
        `Literal` or `Enum` must not go on the wire as an integer/number
        `enum`: spec clients read that shape as a free number and drop the
        choices. Boolean choices are left alone: they have no string-enum
        form and are out of scope here.
        """
        if "enum" in json_schema:
            values = json_schema["enum"]
            if isinstance(values, list) and values:

                def is_number(v: Any) -> bool:
                    return isinstance(v, int | float) and not isinstance(v, bool)

                if any(map(is_number, values)) and all(
                    isinstance(v, str) or is_number(v) for v in values
                ):
                    json_schema["enum"] = [
                        str(v) if is_number(v) else v for v in values
                    ]
                    if json_schema.get("type") in {"integer", "number"}:
                        json_schema["type"] = "string"
                    elif "type" not in json_schema:
                        # Mixed literals (e.g. Literal[1, "a"]) carry no type.
                        json_schema["type"] = "string"
            return json_schema
        if "const" in json_schema:
            value = json_schema["const"]
            if isinstance(value, int | float) and not isinstance(value, bool):
                # A single numeric choice is a one-option string enum, not a
                # `const`: `const` is not a PrimitiveSchemaDefinition shape.
                json_schema.pop("const")
                json_schema["enum"] = [str(value)]
                if json_schema.get("type") in {"integer", "number"}:
                    json_schema["type"] = "string"
        return json_schema

    def enum_schema(self, schema: core_schema.EnumSchema) -> JsonSchemaValue:
        """Generate inline enum schema.

        Always generates enum pattern: `{"enum": [value, ...]}`
        Titled enums are handled separately via dict-based syntax in ctx.elicit().
        """
        # Get the base schema from parent - always use simple enum pattern
        return self._stringify_numeric_enum(super().enum_schema(schema))

    def literal_schema(self, schema: core_schema.LiteralSchema) -> JsonSchemaValue:
        """Generate literal schema, normalizing numeric choices to string enums."""
        return self._stringify_numeric_enum(super().literal_schema(schema))


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
        response_type: The type to validate responses with. For raw schemas,
            it validates the extracted "value".
        is_raw: True if schema was built directly (extract "value" from response)
    """

    schema: dict[str, Any]
    response_type: type | None
    is_raw: bool


#: Raised when a response type is missing. The empty-object schema this used to
#: produce was ambiguous under the MCP spec and left some clients (e.g. VS Code)
#: rendering an empty, non-functional form. Deprecated in 3.2, removed in 4.0.
_NONE_RESPONSE_TYPE_ERROR = (
    "ctx.elicit() requires a response_type. The empty-schema form-mode request "
    "produced by response_type=None was ambiguous under the MCP spec and caused "
    "some clients to render an empty, non-functional form. Pass a type "
    "describing the data you expect back — use `bool` for a confirmation."
)


def parse_elicit_response_type(
    response_type: Any,
    response_title: str | None = None,
    response_description: str | None = None,
) -> ElicitConfig:
    """Parse response_type into schema and handling configuration.

    A response type is required; ``None`` raises ``TypeError``. Supports
    multiple syntaxes:
    - dict: `{"low": {"title": "..."}}` -> single-select titled enum
    - list patterns:
        - `[["a", "b"]]` -> multi-select untitled
        - `[{"low": {...}}]` -> multi-select titled
        - `["a", "b"]` -> single-select untitled
    - `list[X]` type annotation: multi-select with type
    - Scalar types (bool, int, float, str, Literal, Enum): single value
    - Other types (dataclass, BaseModel): use directly

    The ``response_title`` and ``response_description`` arguments customize the
    label and description of the wrapped ``value`` property for the scalar/dict/list
    shorthand forms. They are only valid when FastMCP is wrapping the response
    type; passing them with a full BaseModel/dataclass raises ``TypeError``,
    because in those cases the user already controls field metadata via
    ``Field(title=..., description=...)``.
    """
    has_response_metadata = (
        response_title is not None or response_description is not None
    )

    if response_type is None:
        raise TypeError(_NONE_RESPONSE_TYPE_ERROR)

    if isinstance(response_type, dict):
        config = _parse_dict_syntax(response_type)
    elif isinstance(response_type, list):
        config = _parse_list_syntax(response_type)
    elif get_origin(response_type) is list:
        config = _parse_generic_list(response_type)
    elif _is_scalar_type(response_type):
        config = _parse_scalar_type(response_type)
    else:
        # Other types (dataclass, BaseModel, etc.) - use directly
        if has_response_metadata:
            raise TypeError(
                "response_title and response_description are only supported when "
                "response_type is a scalar, Literal, Enum, or the dict/list "
                "shorthand forms. For BaseModel or dataclass response types, use "
                "Field(title=..., description=...) on the individual fields."
            )
        return ElicitConfig(
            schema=get_elicitation_schema(response_type),
            response_type=response_type,
            is_raw=False,
        )

    if has_response_metadata:
        _apply_value_metadata(config.schema, response_title, response_description)
    return config


def _apply_value_metadata(
    schema: dict[str, Any],
    title: str | None,
    description: str | None,
) -> None:
    """Override title/description on the wrapped ``value`` property in-place."""
    value_schema = schema.get("properties", {}).get("value")
    if value_schema is None:
        return
    if title is not None:
        value_schema["title"] = title
    if description is not None:
        value_schema["description"] = description


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
        response_type=_choice_literal(d),
        is_raw=True,
    )


def _choice_literal(choices: Iterable[str]) -> Any:
    """Build a Literal type that accepts exactly the given choices."""
    return Literal[tuple(choices)]  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]


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
                # The MCP 2025-11-25 schema requires items.type == "string" for
                # multi-select enum properties, matching the list[Literal[...]] form.
                "properties": {
                    "value": {
                        "type": "array",
                        "items": {"type": "string", "enum": lst[0]},
                    }
                },
                "required": ["value"],
            },
            response_type=list[_choice_literal(lst[0])],  # ty:ignore[invalid-type-form]
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
            response_type=list[_choice_literal(lst[0])],  # ty:ignore[invalid-type-form]
            is_raw=True,
        )

    # ["a", "b", "c"] -> single-select untitled
    if lst and all(isinstance(item, str) for item in lst):
        wrapped = ScalarElicitationType[_choice_literal(lst)]  # ty:ignore[invalid-type-form]
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
    # For raw schemas (dict/nested-list syntax), extract and validate the value
    if config.is_raw:
        if not isinstance(content, dict) or "value" not in content:
            raise ValueError("Elicitation response missing required 'value' field.")
        value = content["value"]
        if config.response_type is not None:
            type_adapter = get_cached_typeadapter(config.response_type)
            value = type_adapter.validate_python(value)
        return AcceptedElicitation[Any](data=value)

    # For typed schemas, validate with Pydantic
    if config.response_type is not None:
        type_adapter = get_cached_typeadapter(config.response_type)
        try:
            validated_data = type_adapter.validate_python(content)
        except ValidationError as exc:
            # A spec client answers a string enum with strings, so a
            # stringified numeric choice ("1") fails validation against the
            # numeric type. Retry with exactly the strings the schema
            # offered coerced back to numbers — but only for the fields and
            # list items validation failed on, so a genuine string choice
            # that merely looks numeric ("10") is never rewritten and still
            # validates.
            if not isinstance(content, dict):
                raise
            # Validation error locations use field names when the model sets
            # loc_by_alias=False, while the schema and the wire use aliases.
            # Translate locations to schema property names first.
            alias_map = _field_name_to_schema_key(config.response_type)
            failed: dict[str, set[int] | None] = {}
            for error in exc.errors():
                loc = error.get("loc", ())
                if not loc or not isinstance(loc[0], str):
                    continue
                key = alias_map.get(loc[0], loc[0])
                if len(loc) >= 2 and isinstance(loc[1], int):
                    current = failed.get(key)
                    if current is not None:
                        current.add(loc[1])
                    elif key not in failed:
                        failed[key] = {loc[1]}
                    # A bare loc already recorded means the whole field.
                else:
                    failed[key] = None
            coerced = _coerce_stringified_numeric_enums(content, config.schema, failed)
            if coerced is None:
                raise
            try:
                validated_data = type_adapter.validate_python(coerced)
            except ValidationError:
                raise exc from None
        if isinstance(validated_data, ScalarElicitationType):
            return AcceptedElicitation[Any](data=validated_data.value)
        return AcceptedElicitation[Any](data=validated_data)

    # For None response_type, expect empty response
    if content:
        raise ValueError(
            f"Elicitation expected an empty response, but received: {content}"
        )
    return AcceptedElicitation[dict[str, Any]](data={})


def _parse_number(value: str) -> int | float | None:
    """Parse a stringified numeric enum choice, preferring `int`."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return None


def _schema_key_for_field(field: Any) -> str | None:
    """The schema property name for a model field, if it differs from the
    field name.

    The validation alias names the property, taking precedence over `alias`;
    an `AliasChoices` names it by its first string choice. An `AliasPath`
    or a serialization alias does not rename the property.
    """
    validation_alias = field.validation_alias
    if isinstance(validation_alias, str):
        return validation_alias
    if isinstance(validation_alias, AliasChoices):
        for choice in validation_alias.choices:
            if isinstance(choice, str):
                return choice
            if (
                isinstance(choice, AliasPath)
                and len(choice.path) == 1
                and isinstance(choice.path[0], str)
            ):
                # Pydantic skips multi-segment paths when naming the
                # property, so only a single-segment path can be the key.
                return choice.path[0]
    if isinstance(field.alias, str):
        return field.alias
    return None


def _field_name_to_schema_key(response_type: Any) -> dict[str, str]:
    """Map model field names to the schema property names the wire uses.

    Pydantic schemas name aliased fields by their alias, but validation
    errors report the field name when the model sets `loc_by_alias=False`.
    Only then is a translation needed: with the default `loc_by_alias=True`
    the locations already are aliases, and translating them would
    mistranslate when one field's name collides with another's alias.
    """
    mapping: dict[str, str] = {}
    if isinstance(response_type, type) and issubclass(response_type, BaseModel):
        if response_type.model_config.get("loc_by_alias") is False:
            for name, field in response_type.model_fields.items():
                key = _schema_key_for_field(field)
                if key is not None and key != name:
                    mapping[name] = key
    return mapping


def _coerce_stringified_numeric_enums(
    content: dict[str, Any],
    schema: dict[str, Any],
    fields: dict[str, set[int] | None],
) -> dict[str, Any] | None:
    """Coerce string answers back to the numbers a string enum stands for.

    Only the given fields are touched — a list field only at the given
    item indexes (`None` means the whole field) — and only strings the
    schema itself offered are coerced, so a value the client was never
    offered ("01", "3") is left alone and still rejected. Returns `None`
    when nothing changed.
    """
    props = schema.get("properties", {})
    coerced: dict[str, Any] | None = None
    for key, value in content.items():
        if key not in fields:
            continue
        prop = props.get(key)
        if not isinstance(prop, dict):
            continue
        if (
            isinstance(value, str)
            and prop.get("type") == "string"
            and isinstance(prop.get("enum"), list)
            and value in prop["enum"]
        ):
            number = _parse_number(value)
            if number is None:
                continue
            if coerced is None:
                coerced = dict(content)
            coerced[key] = number
        elif (
            isinstance(value, list)
            and prop.get("type") == "array"
            and isinstance(prop.get("items"), dict)
            and isinstance(prop["items"].get("enum"), list)
        ):
            items_enum = prop["items"]["enum"]
            indexes = fields[key]
            new_list: list[Any] = []
            changed = False
            for index, item in enumerate(value):
                if (
                    (indexes is None or index in indexes)
                    and isinstance(item, str)
                    and item in items_enum
                    and (number := _parse_number(item)) is not None
                ):
                    new_list.append(number)
                    changed = True
                else:
                    new_list.append(item)
            if changed:
                if coerced is None:
                    coerced = dict(content)
                coerced[key] = new_list
    return coerced


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

    # Pydantic emits the internal wrapper class name as a top-level title for
    # ScalarElicitationType schemas. That value is not meaningful on the wire and
    # breaks strict clients (e.g. Codex) that reject unknown top-level fields.
    origin = get_origin(response_type)
    if origin is ScalarElicitationType or response_type is ScalarElicitationType:
        schema.pop("title", None)

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
