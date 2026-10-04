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

## Unsupported regex patterns

Pydantic uses a Rust-based regex engine that does not support all regex
features found in real-world JSON Schemas (particularly those from AWS,
Azure, and other large OpenAPI providers). Unsupported constructs include
lookahead/lookbehind assertions (`(?!...)`, `(?<=...)`), Unicode property
escapes (`\\p{Graph}`, `\\p{Print}`), and very large compiled patterns.

When a `pattern` constraint cannot be compiled, `json_schema_to_type`
degrades gracefully:

1. The pattern is **dropped** from the Pydantic `StringConstraints` so
   the type will not raise a `SchemaError`.
2. A `UserWarning` is emitted with the unsupported pattern.
3. The original pattern is preserved in the type metadata as
   `x-unsupported-pattern` (visible via `TypeAdapter(T).json_schema()`).
4. Other constraints (`minLength`, `maxLength`) are still enforced.

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
import keyword
import re
import threading
import warnings
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import MISSING, dataclass, field, make_dataclass
from datetime import date, datetime
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Generic,
    Literal,
    TypeVar,
    Union,
    cast,
)

from pydantic import (
    AnyUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    Json,
    StringConstraints,
    TypeAdapter,
    create_model,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import SchemaError as _PydanticSchemaError
from typing_extensions import NotRequired, TypedDict

__all__ = ["JSONSchema", "json_schema_to_type"]


def _check_nesting(schema: Any) -> None:
    """Raise `ValueError` if dicts and lists in the schema nest too deeply.

    Normalizing and hashing a schema recurse once per level of nesting. This
    check measures the depth without recursing, so a schema that is too deep
    fails with the same error as one that exceeds the conversion depth limit.
    """
    stack: list[tuple[Any, int]] = [(schema, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_NESTING:
            raise ValueError("JSON schema is too deeply nested to convert")
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)


def _normalize_yaml_types(obj: Any) -> Any:
    """Convert YAML-parsed types back to JSON-native types.

    ``yaml.safe_load`` converts ISO date-time strings to ``datetime``/``date``
    objects.  These crash ``json.dumps`` and produce wrong default values in
    dataclass fields.  This function recursively normalises them to strings.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {
            str(k) if not isinstance(k, str) else k: _normalize_yaml_types(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_normalize_yaml_types(v) for v in obj]
    return obj


def _reject_all(v: Any) -> Any:
    """Validator that rejects every value, implementing JSON Schema `false`."""
    raise ValueError("No value is valid against a false schema")


# JSON Schema `false` means no value is valid. This type rejects everything
# during Pydantic validation.
_UnsatisfiableType = Annotated[Any, BeforeValidator(_reject_all)]

FORMAT_TYPES: dict[str, Any] = {
    "date-time": datetime,
    "email": EmailStr,
    "uri": AnyUrl,
    "json": Json,
}

_V = TypeVar("_V")


class _LRUCache(Generic[_V]):
    """Least recently used cache bounded by entry count and total weight.

    Each entry's weight is the serialized size of the whole schema the
    conversion started from. A generated class refers to the classes of its
    properties and keeps the schema it was built from, so it retains as much
    as the root schema describes. Charging that size keeps the total memory
    held by the cache within `max_weight`. An entry heavier than `max_weight`
    is not stored.
    """

    def __init__(self, max_entries: int, max_weight: int) -> None:
        self.max_entries = max_entries
        self.max_weight = max_weight
        self._items: OrderedDict[Hashable, tuple[_V, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> _V | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: Hashable, value: _V, weight: int) -> None:
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._weight -= previous[1]
            if weight > self.max_weight:
                return
            self._items[key] = (value, weight)
            self._weight += weight
            while len(self._items) > self.max_entries or self._weight > self.max_weight:
                _, (_, evicted_weight) = self._items.popitem(last=False)
                self._weight -= evicted_weight

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._weight = 0

    def __len__(self) -> int:
        return len(self._items)


# Generated classes keyed by (schema hash + root schema hash, class name). Each
# class is weighted by the size of its root schema, so a conversion whose root
# is larger than the weight limit adds nothing to the cache.
_classes: _LRUCache[type] = _LRUCache(max_entries=5000, max_weight=4_000_000)
# TypeAdapters for whole schemas, keyed by schema hash. They hold their
# classes, so they are bounded the same way.
_adapters: _LRUCache[TypeAdapter[Any]] = _LRUCache(
    max_entries=1000, max_weight=4_000_000
)

# Limits on a single conversion. Depth counts nested schemas and `$ref` hops;
# steps count every schema visited.
_MAX_CONVERSION_DEPTH = 64
# Nesting of dicts and lists in the input. Each nested schema takes at most two
# levels (such as `properties` and then the property's schema).
_MAX_NESTING = 4 * _MAX_CONVERSION_DEPTH
_MAX_CONVERSION_STEPS = 50_000


@dataclass
class _Conversion:
    """State for one `json_schema_to_type` call."""

    root_hash: str
    root_size: int
    resolved_refs: dict[str, Any] = field(default_factory=dict)
    building: set[tuple[str, str]] = field(default_factory=set)
    depth: int = 0
    steps: int = 0


_conversion: ContextVar[_Conversion] = ContextVar("json_schema_conversion")


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
    schema: Mapping[str, Any] | bool,
    name: str | None = None,
) -> type:
    """Convert JSON schema to appropriate Python type with validation.

    Args:
        schema: A JSON Schema dictionary defining the type structure and validation rules.
            Boolean schemas are also accepted (``True`` = any type, ``False`` = unsatisfiable).
        name: Optional name for object schemas. Only allowed when schema type is "object".
            If not provided for objects, name will be inferred from schema's "title"
            property or default to "Root".

    Returns:
        A Python type (typically a dataclass for objects) with Pydantic validation

    Raises:
        ValueError: If a name is provided for a non-object schema, or if the
            schema is too deeply nested or too large to convert.

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
    # Boolean schemas (JSON Schema 2020-12 §4.3.2; also valid since draft-06)
    if schema is True:
        return Any
    if schema is False:
        return _UnsatisfiableType  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    _check_nesting(schema)

    # Normalise YAML-parsed types (datetime/date → str, non-str keys → str)
    # so that downstream json.dumps/hashing and default values work correctly.
    return _convert_normalized(_normalize_yaml_types(schema), name)


def _convert_normalized(schema: dict[str, Any], name: str | None) -> type:
    root_hash, root_size = _schema_digest(schema)
    token = _conversion.set(_Conversion(root_hash=root_hash, root_size=root_size))
    try:
        # Always use the top-level schema for references
        if schema.get("type") == "object":
            return _object_schema_to_type(schema, schemas=schema, name=name)
        elif name:
            raise ValueError(f"Can not apply name to non-object schema: {name}")
        result = _schema_to_type(schema, schemas=schema)
        return result  # type: ignore[return-value]  # ty:ignore[invalid-return-type]
    finally:
        _conversion.reset(token)


def json_schema_to_type_adapter(schema: Mapping[str, Any] | bool) -> TypeAdapter[Any]:
    """Return a cached `TypeAdapter` for `json_schema_to_type(schema)`.

    Adapters are cached by schema content in a bounded cache, so the classes
    they hold are released when they are evicted.
    """
    if isinstance(schema, bool):
        return TypeAdapter(json_schema_to_type(schema))
    _check_nesting(schema)
    schema = _normalize_yaml_types(schema)
    key, size = _schema_digest(schema)
    adapter = _adapters.get(key)
    if adapter is None:
        adapter = TypeAdapter(_convert_normalized(schema, None))
        _adapters.put(key, adapter, size)
    return adapter


def _hash_schema(schema: Mapping[str, Any]) -> str:
    """Generate a deterministic hash for schema caching."""
    return _schema_digest(schema)[0]


def _schema_digest(schema: Mapping[str, Any] | bool) -> tuple[str, int]:
    """Return a deterministic hash of the schema and its serialized size.

    Handles non-JSON-native types (datetime, date, bool keys) that can
    appear in schemas loaded from YAML, which auto-parses date strings.
    Uses ``default=str`` for unserializable values and drops ``sort_keys``
    to avoid ``TypeError`` when dicts mix ``bool`` and ``str`` keys.
    """
    try:
        raw = json.dumps(schema, sort_keys=True, default=str)
    except TypeError:
        # Mixed key types (bool + str) can't be sorted; fall back
        raw = json.dumps(schema, default=str)
    encoded = raw.encode()
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


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

    if not constraints:
        return str

    annotated: Any = Annotated[str, StringConstraints(**constraints)]

    if "pattern" in constraints:
        try:
            TypeAdapter(annotated)
        except _PydanticSchemaError as exc:
            if "regex" not in str(exc).lower():
                raise
            pattern = constraints.pop("pattern")
            warnings.warn(
                f"Pattern {pattern!r} is not supported by Pydantic's regex engine "
                f"and will not be enforced.",
                UserWarning,
                stacklevel=2,
            )
            pattern_field = Field(json_schema_extra={"x-unsupported-pattern": pattern})
            if constraints:
                annotated = Annotated[
                    str, StringConstraints(**constraints), pattern_field
                ]  # type: ignore[valid-type]
            else:
                annotated = Annotated[str, pattern_field]  # type: ignore[valid-type]

    return annotated


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

    return Annotated[base, Field(**constraints)] if constraints else base  # type: ignore[return-value]  # ty:ignore[invalid-type-form]


def _create_enum(name: str, values: list[Any]) -> type:
    """Create enum type from list of values."""
    if not values:
        # Empty enum means no value is valid (same semantics as ``false``
        # schema).  Return the unsatisfiable type instead of ``Literal[()]``
        # which triggers an AssertionError in Pydantic.
        return _UnsatisfiableType  # type: ignore[return-value]  # ty:ignore[invalid-return-type]
    # Always return Literal for enum fields to preserve the literal nature
    return Literal[tuple(values)]  # type: ignore[return-value]  # ty:ignore[invalid-type-form]


def _create_array_type(
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str],
) -> type | Annotated[Any, ...]:
    """Create list/set type with optional constraints."""
    items = schema.get("items", {})
    if isinstance(items, list):
        # Handle positional item schemas
        item_types = [_schema_to_type(s, schemas, resolving_refs) for s in items]
        combined = Union[tuple(item_types)]  # noqa: UP007
        base = list[combined]  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]
    else:
        # Handle single item schema
        item_type = _schema_to_type(items, schemas, resolving_refs)
        base_class = set if schema.get("uniqueItems") else list
        base = base_class[item_type]

    constraints = {
        k: v
        for k, v in {
            "min_length": schema.get("minItems"),
            "max_length": schema.get("maxItems"),
        }.items()
        if v is not None
    }

    return Annotated[base, Field(**constraints)] if constraints else base  # type: ignore[return-value]  # ty:ignore[invalid-type-form]


def _return_Any() -> Any:
    return Any


def _object_schema_to_type(
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    name: str | None = None,
    resolving_refs: frozenset[str] = frozenset(),
) -> type:
    """Convert an object schema to the appropriate Python type.

    Single source of truth for the four object-schema cases, used by both the
    top-level ``json_schema_to_type`` entry point and the recursive
    ``_schema_to_type`` path:

    1. No ``properties`` with ``additionalProperties`` truthy — ``dict[str, T]``
       (``T = Any`` when ``additionalProperties is True``, else the value schema's type)
    2. No ``properties`` and no ``additionalProperties`` — ``dict[str, Any]``
    3. Has ``properties`` and ``additionalProperties is True`` — Pydantic model
       (so ``extra="allow"`` can preserve unknown keys)
    4. Has ``properties`` otherwise — dataclass

    ``name`` is used as the generated class name for cases 3 and 4; it falls
    back to the schema's ``title`` when not provided.
    """
    has_properties = bool(schema.get("properties"))
    additional_props = schema.get("additionalProperties")
    class_name = name if name is not None else schema.get("title")

    if not has_properties and additional_props:
        if additional_props is True:
            return dict[str, Any]
        value_type = _schema_to_type(additional_props, schemas, resolving_refs)
        return cast(type[Any], dict[str, value_type])  # type: ignore[valid-type]  # ty:ignore[invalid-type-form]

    if not has_properties and not additional_props:
        return dict[str, Any]

    if has_properties and additional_props is True:
        return _create_pydantic_model(schema, class_name, schemas, resolving_refs)

    return _create_dataclass(schema, class_name, schemas, resolving_refs)


def _get_from_type_handler(
    schema: Mapping[str, Any],
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str],
) -> Callable[..., Any]:
    """Get the appropriate type handler for the schema."""

    type_handlers: dict[str, Callable[..., Any]] = {
        "string": lambda s: _create_string_type(s),
        "integer": lambda s: _create_numeric_type(int, s),
        "number": lambda s: _create_numeric_type(float, s),
        "boolean": lambda _: bool,
        "null": lambda _: type(None),
        "array": lambda s: _create_array_type(s, schemas, resolving_refs),
        "object": lambda s: _object_schema_to_type(
            s, schemas, resolving_refs=resolving_refs
        ),
    }
    return type_handlers.get(schema.get("type", None), _return_Any)


def _schema_to_type(
    schema: Mapping[str, Any] | bool,
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str] = frozenset(),
) -> type | ForwardRef:
    """Convert schema to appropriate Python type, within the conversion limits."""
    conversion = _conversion.get()
    conversion.steps += 1
    if conversion.depth >= _MAX_CONVERSION_DEPTH:
        raise ValueError("JSON schema is too deeply nested to convert")
    if conversion.steps > _MAX_CONVERSION_STEPS:
        raise ValueError("JSON schema is too large to convert")
    conversion.depth += 1
    try:
        return _convert_schema(schema, schemas, resolving_refs)
    finally:
        conversion.depth -= 1


def _convert_schema(
    schema: Mapping[str, Any] | bool,
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str],
) -> type | ForwardRef:
    # Boolean schemas are valid in JSON Schema draft-06+:
    # true means "any value is valid" (equivalent to {}),
    # false means "no value is valid" (unsatisfiable).
    if schema is True:
        return Any
    if schema is False:
        return _UnsatisfiableType  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    if not schema:
        return object

    if "type" not in schema and "properties" in schema:
        return _create_dataclass(schema, schema.get("title", "<unknown>"), schemas)

    # Handle references first
    if "$ref" in schema:
        ref = schema["$ref"]
        # Handle self-reference
        if ref == "#":
            return ForwardRef(_sanitize_name(schema.get("title", "Root")))
        if ref in resolving_refs:
            resolved = _resolve_ref(ref, schemas)
            if isinstance(resolved, Mapping) and (
                resolved.get("type") == "object" or "properties" in resolved
            ):
                return _schema_to_type(resolved, schemas, resolving_refs)
            return Any
        # Convert each reference once per conversion, so a definition shared
        # by many references is not expanded again for every path to it.
        resolved_refs = _conversion.get().resolved_refs
        if ref not in resolved_refs:
            resolved_refs[ref] = _schema_to_type(
                _resolve_ref(ref, schemas), schemas, resolving_refs | {ref}
            )
        return resolved_refs[ref]

    if "const" in schema:
        return Literal[schema["const"]]  # type: ignore

    if "enum" in schema:
        return _create_enum(f"Enum_{len(_classes)}", schema["enum"])

    # Handle anyOf unions
    if "anyOf" in schema:
        types: list[type | Any] = [
            _schema_to_type(subschema, schemas, resolving_refs)
            for subschema in schema["anyOf"]
        ]

        # Check if one of the types is None (null)
        has_null = type(None) in types
        types = [t for t in types if t is not type(None)]

        if len(types) == 0:
            return type(None)
        elif len(types) == 1:
            if has_null:
                return Union[types[0], type(None)]  # type: ignore # noqa: UP007
            else:
                return types[0]
        else:
            if has_null:
                return Union[(*types, type(None))]  # type: ignore
            else:
                return Union[tuple(types)]  # type: ignore # noqa: UP007

    schema_type = schema.get("type")
    if not schema_type:
        return Any

    if isinstance(schema_type, list):
        # Create a copy of the schema for each type, but keep all constraints
        types: list[type | Any] = []
        for t in schema_type:
            type_schema = dict(schema)
            type_schema["type"] = t
            types.append(_schema_to_type(type_schema, schemas, resolving_refs))
        has_null = type(None) in types
        types = [t for t in types if t is not type(None)]
        if has_null:
            if len(types) == 1:
                return Union[types[0], type(None)]  # type: ignore # noqa: UP007
            else:
                return Union[(*types, type(None))]  # type: ignore
        return Union[tuple(types)]  # type: ignore # noqa: UP007

    return _get_from_type_handler(schema, schemas, resolving_refs)(schema)


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
    # Step 6: if result is a Python keyword, append an underscore (PEP 8 convention)
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


_BASE_MODEL_MEMBERS = frozenset(dir(BaseModel))


def _is_reserved_field_name(name: str) -> bool:
    """Whether a property name would act as a class or Pydantic control name.

    Leading underscores cover dunders, Pydantic private attributes, and the
    `create_model` keyword arguments. Members in the `model_` namespace are
    Pydantic's own API.
    """
    return name.startswith("_") or (
        name.startswith("model_") and name in _BASE_MODEL_MEMBERS
    )


def safe_create_model(
    name: str,
    field_definitions: Mapping[str, tuple[Any, Any]],
    *,
    config: ConfigDict | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model whose field names come from untrusted input.

    Each property name only ever becomes a field. A reserved name is stored
    under a generated field name with the original name as its alias, so it
    validates and dumps (with `by_alias=True`) under its original name.

    Args:
        name: The model class name.
        field_definitions: Maps each property name to `(annotation, default)`,
            where `default` may be a `FieldInfo` or `...` for required fields.
        config: Optional model configuration.
    """
    fields: dict[str, Any] = {}
    for prop_name, (annotation, default) in field_definitions.items():
        field_name = prop_name
        if _is_reserved_field_name(prop_name):
            base = f"field_{_sanitize_name(prop_name).lstrip('_')}"
            field_name = base
            counter = 2
            while field_name in fields or field_name in field_definitions:
                field_name = f"{base}_{counter}"
                counter += 1
            if isinstance(default, FieldInfo):
                annotation = Annotated[annotation, default]
                default = Field(alias=prop_name)
            else:
                default = Field(default=default, alias=prop_name)
        fields[field_name] = (annotation, default)
    return create_model(name, __config__=config, **fields)


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
    resolving_refs: frozenset[str] = frozenset(),
) -> type:
    """Create Pydantic BaseModel from object schema with additionalProperties."""
    name = name or schema.get("title", "Root")
    if name is None:
        raise ValueError("Name is required")
    sanitized_name = _sanitize_name(name)
    conversion = _conversion.get()
    schema_hash = _hash_schema(schema)
    cache_key = (schema_hash + conversion.root_hash, sanitized_name)

    # Return existing class if already built
    existing = _classes.get(cache_key)
    if existing is not None:
        return existing
    # A recursive reference to a class that is still being built
    if cache_key in conversion.building:
        return ForwardRef(sanitized_name)  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    conversion.building.add(cache_key)
    try:
        cls = _build_pydantic_model(
            schema, sanitized_name, schemas or {}, resolving_refs
        )
    finally:
        conversion.building.discard(cache_key)
    _classes.put(cache_key, cls, conversion.root_size)
    return cls


def _build_pydantic_model(
    schema: Mapping[str, Any],
    name: str,
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str],
) -> type:
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    field_definitions: dict[str, tuple[Any, Any]] = {}
    for prop_name, prop_schema in properties.items():
        # Boolean schemas (JSON Schema draft-06+): resolve type directly,
        # then use an empty dict for .get() calls below.
        if isinstance(prop_schema, bool):
            field_type = _schema_to_type(prop_schema, schemas, resolving_refs)
            prop_schema = {}
        else:
            field_type = _schema_to_type(prop_schema, schemas, resolving_refs)

        # Handle defaults
        default_value = prop_schema.get("default", MISSING)
        if default_value is not MISSING:
            field_definitions[prop_name] = (field_type, default_value)
        elif prop_name in required:
            field_definitions[prop_name] = (field_type, ...)
        else:
            field_definitions[prop_name] = (Union[field_type, type(None)], None)  # type: ignore[misc]  # noqa: UP007  # ty:ignore[invalid-type-form]

    return safe_create_model(name, field_definitions, config=ConfigDict(extra="allow"))


def _create_dataclass(
    schema: Mapping[str, Any],
    name: str | None = None,
    schemas: Mapping[str, Any] | None = None,
    resolving_refs: frozenset[str] = frozenset(),
) -> type:
    """Create dataclass from object schema."""
    name = name or schema.get("title", "Root")
    # Sanitize name for class creation
    if name is None:
        raise ValueError("Name is required")
    sanitized_name = _sanitize_name(name)
    conversion = _conversion.get()
    schema_hash = _hash_schema(schema)
    cache_key = (schema_hash + conversion.root_hash, sanitized_name)

    # Return existing class if already built
    existing = _classes.get(cache_key)
    if existing is not None:
        return existing
    # A recursive reference to a class that is still being built
    if cache_key in conversion.building:
        return ForwardRef(sanitized_name)  # type: ignore[return-value]  # ty:ignore[invalid-return-type]

    conversion.building.add(cache_key)
    try:
        cls = _build_dataclass(schema, sanitized_name, schemas or {}, resolving_refs)
    finally:
        conversion.building.discard(cache_key)
    if isinstance(cls, type):
        _classes.put(cache_key, cls, conversion.root_size)
    return cls


def _build_dataclass(
    schema: Mapping[str, Any],
    sanitized_name: str,
    schemas: Mapping[str, Any],
    resolving_refs: frozenset[str],
) -> Any:
    original_schema = dict(schema)  # Store copy for validator

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref == "#":
            return ForwardRef(sanitized_name)
        if ref in resolving_refs:
            return Any
        schema = _resolve_ref(ref, schemas)
        resolving_refs = resolving_refs | {ref}

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    fields: list[tuple[Any, ...]] = []
    used_field_names: set[str] = set()
    for prop_name, prop_schema in properties.items():
        field_name = _sanitize_name(prop_name)
        # Deduplicate: if sanitized names collide (e.g. "foo-bar" and
        # "foo_bar" both become "foo_bar"), append a numeric suffix.
        base = field_name
        counter = 2
        while field_name in used_field_names:
            field_name = f"{base}_{counter}"
            counter += 1
        used_field_names.add(field_name)

        # Boolean schemas (JSON Schema draft-06+): resolve type directly,
        # then use an empty dict for .get() calls below.
        if isinstance(prop_schema, bool):
            field_type = _schema_to_type(prop_schema, schemas, resolving_refs)
            prop_schema = {}
        elif prop_schema.get("$ref") == "#":
            # Check for self-reference in property
            field_type = ForwardRef(sanitized_name)
        else:
            field_type = _schema_to_type(prop_schema, schemas, resolving_refs)

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
            fields.append((field_name, Union[field_type, type(None)], field_def))  # type: ignore[misc]  # noqa: UP007  # ty:ignore[invalid-type-form]

    cls = make_dataclass(sanitized_name, fields, kw_only=True)

    # Add model validator for defaults
    @model_validator(mode="before")
    @classmethod
    def _apply_defaults(cls, data: Mapping[str, Any]):
        if isinstance(data, dict):
            return _merge_defaults(data, original_schema)
        return data

    cls._apply_defaults = _apply_defaults  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
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
        # Normalize boolean schemas (JSON Schema draft-06+)
        if isinstance(prop_schema, bool):
            continue

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
