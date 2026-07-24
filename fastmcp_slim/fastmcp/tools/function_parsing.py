"""Function introspection and schema generation for FastMCP tools."""

from __future__ import annotations

import functools
import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Generic, Union, get_args, get_origin, get_type_hints

import mcp_types
from pydantic import BaseModel, PydanticSchemaGenerationError
from typing_extensions import TypeAliasType
from typing_extensions import TypeVar as TypeVarExt

from fastmcp.tools.base import ToolResult, resolve_serialize_by_alias
from fastmcp.utilities.docstring_parsing import ParsedDocstring, parse_docstring
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    get_cached_typeadapter,
    is_class_member_of_type,
    replace_type,
)

try:
    from prefab_ui.app import PrefabApp as _PrefabApp
    from prefab_ui.components.base import Component as _PrefabComponent

    _PREFAB_TYPES: tuple[type, ...] = (_PrefabApp, _PrefabComponent)
except ImportError:
    _PREFAB_TYPES = ()


def _contains_bytes_type(tp: Any) -> bool:
    """Check if *tp* is or contains bytes, recursing through unions and Annotated."""
    if tp is bytes:
        return True
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType or origin is Annotated:
        return any(_contains_bytes_type(a) for a in get_args(tp))
    return False


def _contains_prefab_type(tp: Any) -> bool:
    """Check if *tp* is or contains a prefab type, recursing through unions and Annotated."""
    if isinstance(tp, type) and issubclass(tp, _PREFAB_TYPES):
        return True
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType or origin is Annotated:
        return any(_contains_prefab_type(a) for a in get_args(tp))
    return False


def _unwrap_type_alias(tp: Any) -> Any:
    """Resolve a PEP 695 ``type X = ...`` alias to its underlying value.

    ``get_origin()`` returns ``None`` for a ``TypeAliasType``, so an alias that
    factors out a guard union (``type Result = str | InputRequiredResult``) — or
    a lone aliased arm — would otherwise slip past union detection. Resolving to
    ``__value__`` (repeatedly, for chained aliases) restores the concrete type.
    """
    while isinstance(tp, TypeAliasType):
        tp = tp.__value__
    return tp


def _is_input_required_type(tp: Any) -> bool:
    """True when *tp* is the `InputRequiredResult` type (SEP-2322).

    Resolves a `TypeAliasType` and peels an `Annotated` wrapper first, so an
    aliased arm or a metadata-carrying arm such as
    ``Annotated[InputRequiredResult, Field(...)]`` is recognized as a guard
    signal, not just the bare class.
    """
    tp = _unwrap_type_alias(tp)
    if get_origin(tp) is Annotated:
        tp = _unwrap_type_alias(get_args(tp)[0])
    return isinstance(tp, type) and issubclass(tp, mcp_types.InputRequiredResult)


def _contains_input_required(tp: Any) -> bool:
    """True when `InputRequiredResult` appears anywhere in *tp*.

    Recurses through `TypeAliasType`, `Annotated`, and unions so a guard arm is
    found even when factored through a composed alias (``str | Value`` where
    ``Value = int | InputRequiredResult``).
    """
    tp = _unwrap_type_alias(tp)
    if _is_input_required_type(tp):
        return True
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType or origin is Annotated:
        return any(_contains_input_required(a) for a in get_args(tp))
    return False


def _residual_union_arms(tp: Any) -> list[Any]:
    """Flatten a (possibly aliased/nested) union into its non-guard arms.

    Every `InputRequiredResult` arm is dropped at any depth, and aliased union
    arms are flattened inline so the residual is a flat union of data arms.
    """
    arms: list[Any] = []
    for arm in get_args(_unwrap_type_alias(tp)):
        if _is_input_required_type(arm):
            continue
        unwrapped = _unwrap_type_alias(arm)
        arm_origin = get_origin(unwrapped)
        if arm_origin is Union or arm_origin is types.UnionType:
            arms.extend(_residual_union_arms(unwrapped))
        elif arm_origin is Annotated:
            arms.append(_strip_input_required(arm))
        else:
            arms.append(arm)
    return arms


def _strip_input_required(tp: Any) -> Any:
    """Remove `InputRequiredResult` arms from a union return annotation.

    A guard tool typically annotates its return as ``X | InputRequiredResult``;
    the ``InputRequiredResult`` arm is a suspend signal, not output data, so it
    is dropped before schema derivation. Stripping recurses through
    `TypeAliasType` and nested unions, so a guard arm factored through an alias
    (even ``str | Value`` where ``Value = int | InputRequiredResult``) is still
    removed. A non-union annotation, or one with no such arm, is returned
    unchanged. A bare ``InputRequiredResult`` annotation (no other arm) is left
    intact and suppressed downstream like other non-serializable return types.
    """
    if not _contains_input_required(tp):
        return tp
    unwrapped = _unwrap_type_alias(tp)
    origin = get_origin(unwrapped)
    if origin is Annotated:
        # Annotated[X | InputRequiredResult, meta] — strip inside, keep metadata.
        inner, *metadata = get_args(unwrapped)
        return Annotated[(_strip_input_required(inner), *metadata)]
    if origin is not Union and origin is not types.UnionType:
        # A bare InputRequiredResult (possibly via a `type X = ...` alias): left
        # intact, but returned de-aliased so the downstream subclass/exact-type
        # suppression recognizes it and emits no output schema.
        return unwrapped
    residual = _residual_union_arms(unwrapped)
    if not residual:
        return tp
    if len(residual) == 1:
        return residual[0]
    return Union[tuple(residual)]  # noqa: UP007


def _unwrap_model(tp: Any) -> type[BaseModel] | None:
    """Unwrap ``Annotated`` and return the underlying Pydantic model, if any."""
    if get_origin(tp) is Annotated:
        return _unwrap_model(get_args(tp)[0])
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return tp
    return None


def _resolve_output_by_alias(tp: Any) -> bool:
    """Resolve ``by_alias`` for the output schema of return type *tp*.

    Unwraps ``Annotated`` and ``Optional``/``Union`` wrappers to find the
    underlying Pydantic model so the generated schema honors the model's
    ``serialize_by_alias`` config — keeping it consistent with how the runtime
    result is serialized. Containers (``list[Model]`` etc.) are not unwrapped:
    their schema keeps the default, matching the runtime path which only
    special-cases a directly-returned model.

    Known limitation: a single schema is generated with one ``by_alias`` value,
    while the runtime resolves the alias mode per returned value. They cannot
    diverge for a plain single-model return, but a union return can produce more
    than one runtime alias mode that no single schema can describe:

    - distinct models with *conflicting* ``serialize_by_alias`` (e.g. ``A | B``
      where ``A`` opts out but ``B`` opts in), and
    - a model arm alongside a container arm (e.g. ``Model | list[Model]``):
      a directly-returned model honors its config, but a returned ``list`` is
      serialized with the default alias mode, so the two variants disagree.

    Pydantic's schema generator does not consult per-model ``serialize_by_alias``
    and the runtime does not recurse into containers, so honoring every variant
    would require per-arm schema assembly. This is an accepted edge; single-model
    returns and unions whose arms all resolve to the same mode are consistent.
    """
    origin = get_origin(tp)
    if origin is Annotated:
        return _resolve_output_by_alias(get_args(tp)[0])
    if origin is Union or origin is types.UnionType:
        for arg in get_args(tp):
            model = _unwrap_model(arg)
            if model is not None:
                return resolve_serialize_by_alias(model)
        return True
    return resolve_serialize_by_alias(tp)


T = TypeVarExt("T", default=Any)

logger = get_logger(__name__)


@dataclass
class _WrappedResult(Generic[T]):
    result: T


class _UnserializableType:
    pass


def _is_object_schema(
    schema: dict[str, Any],
    *,
    _root_schema: dict[str, Any] | None = None,
    _seen_refs: set[str] | None = None,
) -> bool:
    """Check if a JSON schema represents an object type."""
    root_schema = _root_schema or schema
    seen_refs = _seen_refs or set()

    # Direct object type
    if schema.get("type") == "object":
        return True

    # Schema with properties but no explicit type is treated as object
    if "properties" in schema:
        return True

    # Resolve local $ref definitions and recurse into the target schema.
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return False

    if ref in seen_refs:
        return False

    # Walk the JSON Pointer path from the root schema, unescaping each
    # token per RFC 6901 (~1 → /, ~0 → ~).
    pointer = ref.removeprefix("#/")
    segments = pointer.split("/")
    target: Any = root_schema
    for segment in segments:
        unescaped = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or unescaped not in target:
            return False
        target = target[unescaped]

    target_schema = target
    if not isinstance(target_schema, dict):
        return False

    return _is_object_schema(
        target_schema,
        _root_schema=root_schema,
        _seen_refs=seen_refs | {ref},
    )


@dataclass
class ParsedFunction:
    fn: Callable[..., Any]
    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    return_type: Any = None

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        validate: bool = True,
        wrap_non_object_output_schema: bool = True,
    ) -> ParsedFunction:
        if validate:
            sig = inspect.signature(fn)
            # Reject signatures that cannot be represented by MCP's
            # object-shaped tool arguments.
            for param in sig.parameters.values():
                if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                    raise ValueError(
                        "Functions with positional-only parameters are not "
                        "supported as tools because MCP passes tool arguments by "
                        "name. Replace them with standard parameters that can be "
                        "passed as keywords."
                    )
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    raise ValueError("Functions with *args are not supported as tools")
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    raise ValueError(
                        "Functions with **kwargs are not supported as tools"
                    )

        # collect name and description before we potentially modify the function
        fn_name = getattr(fn, "__name__", None) or fn.__class__.__name__
        outer_docstring = parse_docstring(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn) and not isinstance(fn, functools.partial):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # For callable classes, parameter descriptions must come from
        # __call__'s docstring — where the exposed parameters are actually
        # declared. The class docstring's Args section, if any, typically
        # describes __init__, so falling back to it would risk injecting
        # constructor docs into __call__'s schema on overlapping names.
        # The description, however, comes from the class docstring (which
        # describes what the tool IS) when present.
        inner_docstring = parse_docstring(fn)
        parsed_docstring = ParsedDocstring(
            description=outer_docstring.description or inner_docstring.description,
            parameters=inner_docstring.parameters,
        )

        # Transform Context type annotations to Depends() for unified DI
        from fastmcp.server.dependencies import (
            transform_context_annotations,
            without_injected_parameters,
        )

        fn = transform_context_annotations(fn)

        # Handle injected parameters (Context, Docket dependencies)
        wrapper_fn = without_injected_parameters(fn)

        input_type_adapter = get_cached_typeadapter(wrapper_fn)
        input_schema = input_type_adapter.json_schema()

        input_schema = compress_schema(input_schema, prune_titles=True)

        # Inject parameter descriptions from the docstring into the schema.
        # Explicit annotations (Field(description=...), Annotated[x, "..."])
        # already have a "description" key and take precedence.
        if parsed_docstring.parameters:
            properties = input_schema.get("properties", {})
            for param_name, param_desc in parsed_docstring.parameters.items():
                if (
                    param_name in properties
                    and "description" not in properties[param_name]
                ):
                    properties[param_name]["description"] = param_desc

        # Auto-populate the create-then-pass contract onto `SessionId`-annotated
        # parameters so an agent learns it straight from the schema. Append to any
        # author-provided description rather than clobbering it.
        from fastmcp.server.sessions import (
            SESSION_ID_DESCRIPTION,
            session_id_parameter_names,
        )

        properties = input_schema.get("properties", {})
        for param_name in session_id_parameter_names(fn):
            if param_name not in properties:
                continue
            existing = properties[param_name].get("description")
            if not existing:
                properties[param_name]["description"] = SESSION_ID_DESCRIPTION
            elif SESSION_ID_DESCRIPTION not in existing:
                properties[param_name]["description"] = (
                    f"{existing}\n\n{SESSION_ID_DESCRIPTION}"
                )

        output_schema = None
        # Get the return annotation from the signature
        sig = inspect.signature(fn)
        output_type = sig.return_annotation

        # If the annotation is a string (from __future__ annotations), resolve it
        if isinstance(output_type, str):
            try:
                # Use get_type_hints to resolve the return type
                # include_extras=True preserves Annotated metadata
                type_hints = get_type_hints(fn, include_extras=True)
                output_type = type_hints.get("return", output_type)
            except Exception as e:
                # If resolution fails, keep the string annotation
                logger.debug("Failed to resolve type hint for return annotation: %s", e)

        # Save original for return_type before any schema-related replacement
        original_output_type = output_type

        # An `InputRequiredResult` return arm (SEP-2322 guard tools) is a
        # control-flow signal, not data: strip it so the residual arms drive
        # output-schema derivation (mirrors the SDK's func_metadata). The tool
        # body still returns it at runtime; the tool pipeline passes it through
        # to the wire without touching the output schema.
        output_type = _strip_input_required(output_type)

        if output_type not in (inspect._empty, None, Any, ...):
            # bytes can't be represented as structured JSON output — skip schema
            if _contains_bytes_type(output_type):
                output_type = _UnserializableType

            # Prefab component subclasses (Column, Card, etc.) shouldn't
            # produce output schemas — replace_type only does exact matching,
            # so we handle subclass matching explicitly here.  We also need
            # to handle composite types like ``Column | None`` and
            # ``Annotated[PrefabApp, ...]`` by recursing into their args.
            if _PREFAB_TYPES and _contains_prefab_type(output_type):
                output_type = _UnserializableType

            # ToolResult subclasses should suppress schema generation just
            # like ToolResult itself — replace_type only does exact matching.
            if is_class_member_of_type(output_type, ToolResult):
                output_type = _UnserializableType

            # A bare CallToolResult gives the tool full protocol-level control
            # over its response, so there is no FastMCP output schema to infer.
            if isinstance(output_type, type) and issubclass(
                output_type, mcp_types.CallToolResult
            ):
                output_type = _UnserializableType

            # If InputRequiredResult survives stripping in any wrapping — bare,
            # via a `type X = ...` alias, Annotated, or a subclass — it is a
            # guard-only return with no output data (a union would have had its
            # guard arms stripped above). Suppress the schema wholesale; matching
            # `run()`'s subclass-aware control handling and covering every alias
            # shape that exact-match replace_type below would miss.
            if _contains_input_required(output_type):
                output_type = _UnserializableType

            # there are a variety of types that we don't want to attempt to
            # serialize because they are either used by FastMCP internally,
            # or are MCP content types that explicitly don't form structured
            # content. By replacing them with an explicitly unserializable type,
            # we ensure that no output schema is automatically generated.
            clean_output_type = replace_type(
                output_type,
                dict.fromkeys(
                    (
                        Image,
                        Audio,
                        File,
                        ToolResult,
                        mcp_types.TextContent,
                        mcp_types.ImageContent,
                        mcp_types.AudioContent,
                        mcp_types.ResourceLink,
                        mcp_types.EmbeddedResource,
                        # A guard tool's suspend signal is control flow, not
                        # output data (any residual bare arm is suppressed).
                        mcp_types.InputRequiredResult,
                        *_PREFAB_TYPES,
                    ),
                    _UnserializableType,
                ),
            )

            try:
                # Honor the model's serialize_by_alias config so the schema's
                # field names match the serialized result (see base.py).
                by_alias = _resolve_output_by_alias(clean_output_type)
                type_adapter = get_cached_typeadapter(clean_output_type)
                base_schema = type_adapter.json_schema(
                    mode="serialization", by_alias=by_alias
                )

                # Generate schema for wrapped type if it's non-object
                # because MCP requires that output schemas are objects
                # Check if schema is an object type, resolving $ref references
                # (self-referencing types use $ref at root level)
                if wrap_non_object_output_schema and not _is_object_schema(base_schema):
                    # Use the wrapped result schema directly
                    wrapped_type = _WrappedResult[clean_output_type]
                    wrapped_adapter = get_cached_typeadapter(wrapped_type)
                    output_schema = wrapped_adapter.json_schema(
                        mode="serialization", by_alias=by_alias
                    )
                    output_schema["x-fastmcp-wrap-result"] = True
                else:
                    output_schema = base_schema

                output_schema = compress_schema(output_schema, prune_titles=True)

            except PydanticSchemaGenerationError as e:
                if "_UnserializableType" not in str(e):
                    logger.debug(f"Unable to generate schema for type {output_type!r}")

        return cls(
            fn=fn,
            name=fn_name,
            description=parsed_docstring.description,
            input_schema=input_schema,
            output_schema=output_schema or None,
            return_type=original_output_type,
        )
