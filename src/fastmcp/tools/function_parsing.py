"""Function introspection and schema generation for FastMCP tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, get_type_hints

import mcp.types
from pydantic import PydanticSchemaGenerationError
from typing_extensions import TypeVar as TypeVarExt

from fastmcp.server.dependencies import (
    transform_context_annotations,
    without_injected_parameters,
)
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    create_function_without_params,
    get_cached_typeadapter,
    replace_type,
)

T = TypeVarExt("T", default=Any)

logger = get_logger(__name__)


@dataclass
class _WrappedResult(Generic[T]):
    """Generic wrapper for non-object return types."""

    result: T


class _UnserializableType:
    pass


def _is_object_schema(schema: dict[str, Any]) -> bool:
    """Check if a JSON schema represents an object type."""
    # Direct object type
    if schema.get("type") == "object":
        return True

    # Schema with properties but no explicit type is treated as object
    if "properties" in schema:
        return True

    # Self-referencing types use $ref pointing to $defs
    # The referenced type is always an object in our use case
    return "$ref" in schema and "$defs" in schema


@dataclass
class ParsedFunction:
    fn: Callable[..., Any]
    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        exclude_args: list[str] | None = None,
        validate: bool = True,
        wrap_non_object_output_schema: bool = True,
    ) -> ParsedFunction:
        if validate:
            sig = inspect.signature(fn)
            # Reject functions with *args or **kwargs
            for param in sig.parameters.values():
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    raise ValueError("Functions with *args are not supported as tools")
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    raise ValueError(
                        "Functions with **kwargs are not supported as tools"
                    )

            # Reject exclude_args that don't exist in the function or don't have a default value
            if exclude_args:
                for arg_name in exclude_args:
                    if arg_name not in sig.parameters:
                        raise ValueError(
                            f"Parameter '{arg_name}' in exclude_args does not exist in function."
                        )
                    param = sig.parameters[arg_name]
                    if param.default == inspect.Parameter.empty:
                        raise ValueError(
                            f"Parameter '{arg_name}' in exclude_args must have a default value."
                        )

        # collect name and doc before we potentially modify the function
        fn_name = getattr(fn, "__name__", None) or fn.__class__.__name__
        fn_doc = inspect.getdoc(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        # Handle injected parameters (Context, Docket dependencies)
        wrapper_fn = without_injected_parameters(fn)

        # Also handle exclude_args with non-serializable types (issue #2431)
        # This must happen before Pydantic tries to serialize the parameters
        if exclude_args:
            wrapper_fn = create_function_without_params(wrapper_fn, list(exclude_args))

        input_type_adapter = get_cached_typeadapter(wrapper_fn)
        input_schema = input_type_adapter.json_schema()

        # Compress and handle exclude_args
        prune_params = list(exclude_args) if exclude_args else None
        input_schema = compress_schema(
            input_schema, prune_params=prune_params, prune_titles=True
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

        if output_type not in (inspect._empty, None, Any, ...):
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
                        mcp.types.TextContent,
                        mcp.types.ImageContent,
                        mcp.types.AudioContent,
                        mcp.types.ResourceLink,
                        mcp.types.EmbeddedResource,
                    ),
                    _UnserializableType,
                ),
            )

            try:
                type_adapter = get_cached_typeadapter(clean_output_type)
                base_schema = type_adapter.json_schema(mode="serialization")

                # Generate schema for wrapped type if it's non-object
                # because MCP requires that output schemas are objects
                # Check if schema is an object type, resolving $ref references
                # (self-referencing types use $ref at root level)
                if wrap_non_object_output_schema and not _is_object_schema(base_schema):
                    # Use the wrapped result schema directly
                    wrapped_type = _WrappedResult[clean_output_type]
                    wrapped_adapter = get_cached_typeadapter(wrapped_type)
                    output_schema = wrapped_adapter.json_schema(mode="serialization")
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
            description=fn_doc,
            input_schema=input_schema,
            output_schema=output_schema or None,
        )
