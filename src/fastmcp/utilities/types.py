"""Common types used across FastMCP."""

import base64
import inspect
import mimetypes
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from types import EllipsisType, UnionType
from typing import (
    Annotated,
    TypeAlias,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import mcp.types
from mcp.types import Annotations
from pydantic import AnyUrl, BaseModel, ConfigDict, Field, TypeAdapter, UrlConstraints

T = TypeVar("T")

# sentinel values for optional arguments
NotSet = ...
NotSetT: TypeAlias = EllipsisType


class FastMCPBaseModel(BaseModel):
    """Base model for FastMCP models."""

    model_config = ConfigDict(extra="forbid")


@lru_cache(maxsize=5000)
def get_cached_typeadapter(cls: T) -> TypeAdapter[T]:
    """
    TypeAdapters are heavy objects, and in an application context we'd typically
    create them once in a global scope and reuse them as often as possible.
    However, this isn't feasible for user-generated functions. Instead, we use a
    cache to minimize the cost of creating them as much as possible.
    """
    # For functions, process annotations to handle forward references and convert
    # Annotated[Type, "string"] to Annotated[Type, Field(description="string")]
    if inspect.isfunction(cls) or inspect.ismethod(cls):
        if hasattr(cls, "__annotations__") and cls.__annotations__:
            try:
                # Resolve forward references first
                resolved_hints = get_type_hints(cls, include_extras=True)
            except Exception:
                # If forward reference resolution fails, use original annotations
                resolved_hints = cls.__annotations__

            # Process annotations to convert string descriptions to Fields
            processed_hints = {}

            for name, annotation in resolved_hints.items():
                # Check if this is Annotated[Type, "string"] and convert to Annotated[Type, Field(description="string")]
                if (
                    get_origin(annotation) is Annotated
                    and len(get_args(annotation)) == 2
                    and isinstance(get_args(annotation)[1], str)
                ):
                    base_type, description = get_args(annotation)
                    processed_hints[name] = Annotated[
                        base_type, Field(description=description)
                    ]
                else:
                    processed_hints[name] = annotation

            # Create new function if annotations changed
            if processed_hints != cls.__annotations__:
                import types

                # Handle both functions and methods
                if inspect.ismethod(cls):
                    actual_func = cls.__func__
                    code = actual_func.__code__
                    globals_dict = actual_func.__globals__
                    name = actual_func.__name__
                    defaults = actual_func.__defaults__
                    closure = actual_func.__closure__
                else:
                    code = cls.__code__
                    globals_dict = cls.__globals__
                    name = cls.__name__
                    defaults = cls.__defaults__
                    closure = cls.__closure__

                new_func = types.FunctionType(
                    code,
                    globals_dict,
                    name,
                    defaults,
                    closure,
                )
                new_func.__dict__.update(cls.__dict__)
                new_func.__module__ = cls.__module__
                new_func.__qualname__ = getattr(cls, "__qualname__", cls.__name__)
                new_func.__annotations__ = processed_hints

                if inspect.ismethod(cls):
                    new_method = types.MethodType(new_func, cls.__self__)
                    return TypeAdapter(new_method)
                else:
                    return TypeAdapter(new_func)

    return TypeAdapter(cls)


def issubclass_safe(cls: type, base: type) -> bool:
    """Check if cls is a subclass of base, even if cls is a type variable."""
    try:
        if origin := get_origin(cls):
            return issubclass_safe(origin, base)
        return issubclass(cls, base)
    except TypeError:
        return False


def is_class_member_of_type(cls: type, base: type) -> bool:
    """
    Check if cls is a member of base, even if cls is a type variable.

    Base can be a type, a UnionType, or an Annotated type. Generic types are not
    considered members (e.g. T is not a member of list[T]).
    """
    origin = get_origin(cls)
    # Handle both types of unions: UnionType (from types module, used with | syntax)
    # and typing.Union (used with Union[] syntax)
    if origin is UnionType or origin == Union:
        return any(is_class_member_of_type(arg, base) for arg in get_args(cls))
    elif origin is Annotated:
        # For Annotated[T, ...], check if T is a member of base
        args = get_args(cls)
        if args:
            return is_class_member_of_type(args[0], base)
        return False
    else:
        return issubclass_safe(cls, base)


def find_kwarg_by_type(fn: Callable, kwarg_type: type) -> str | None:
    """
    Find the name of the kwarg that is of type kwarg_type.

    Includes union types that contain the kwarg_type, as well as Annotated types.
    """
    if inspect.ismethod(fn) and hasattr(fn, "__func__"):
        fn = fn.__func__

    # Try to get resolved type hints
    try:
        # Use include_extras=True to preserve Annotated metadata
        type_hints = get_type_hints(fn, include_extras=True)
    except Exception:
        # If resolution fails, use raw annotations if they exist
        type_hints = getattr(fn, "__annotations__", {})

    sig = inspect.signature(fn)
    for name, param in sig.parameters.items():
        # Use resolved hint if available, otherwise raw annotation
        annotation = type_hints.get(name, param.annotation)
        if is_class_member_of_type(annotation, kwarg_type):
            return name
    return None


class Image:
    """Helper class for returning images from tools."""

    def __init__(
        self,
        path: str | Path | None = None,
        data: bytes | None = None,
        format: str | None = None,
        annotations: Annotations | None = None,
    ):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        if path is not None and data is not None:
            raise ValueError("Only one of path or data can be provided")

        self.path = Path(os.path.expandvars(str(path))).expanduser() if path else None
        self.data = data
        self._format = format
        self._mime_type = self._get_mime_type()
        self.annotations = annotations

    def _get_mime_type(self) -> str:
        """Get MIME type from format or guess from file extension."""
        if self._format:
            return f"image/{self._format.lower()}"

        if self.path:
            suffix = self.path.suffix.lower()
            return {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(suffix, "application/octet-stream")
        return "image/png"  # default for raw binary data

    def to_image_content(
        self,
        mime_type: str | None = None,
        annotations: Annotations | None = None,
    ) -> mcp.types.ImageContent:
        """Convert to MCP ImageContent."""
        if self.path:
            with open(self.path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        elif self.data is not None:
            data = base64.b64encode(self.data).decode()
        else:
            raise ValueError("No image data available")

        return mcp.types.ImageContent(
            type="image",
            data=data,
            mimeType=mime_type or self._mime_type,
            annotations=annotations or self.annotations,
        )


class Audio:
    """Helper class for returning audio from tools."""

    def __init__(
        self,
        path: str | Path | None = None,
        data: bytes | None = None,
        format: str | None = None,
        annotations: Annotations | None = None,
    ):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        if path is not None and data is not None:
            raise ValueError("Only one of path or data can be provided")

        self.path = Path(os.path.expandvars(str(path))).expanduser() if path else None
        self.data = data
        self._format = format
        self._mime_type = self._get_mime_type()
        self.annotations = annotations

    def _get_mime_type(self) -> str:
        """Get MIME type from format or guess from file extension."""
        if self._format:
            return f"audio/{self._format.lower()}"

        if self.path:
            suffix = self.path.suffix.lower()
            return {
                ".wav": "audio/wav",
                ".mp3": "audio/mpeg",
                ".ogg": "audio/ogg",
                ".m4a": "audio/mp4",
                ".flac": "audio/flac",
            }.get(suffix, "application/octet-stream")
        return "audio/wav"  # default for raw binary data

    def to_audio_content(
        self,
        mime_type: str | None = None,
        annotations: Annotations | None = None,
    ) -> mcp.types.AudioContent:
        if self.path:
            with open(self.path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        elif self.data is not None:
            data = base64.b64encode(self.data).decode()
        else:
            raise ValueError("No audio data available")

        return mcp.types.AudioContent(
            type="audio",
            data=data,
            mimeType=mime_type or self._mime_type,
            annotations=annotations or self.annotations,
        )


class File:
    """Helper class for returning audio from tools."""

    def __init__(
        self,
        path: str | Path | None = None,
        data: bytes | None = None,
        format: str | None = None,
        name: str | None = None,
        annotations: Annotations | None = None,
    ):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        if path is not None and data is not None:
            raise ValueError("Only one of path or data can be provided")

        self.path = Path(os.path.expandvars(str(path))).expanduser() if path else None
        self.data = data
        self._format = format
        self._mime_type = self._get_mime_type()
        self._name = name
        self.annotations = annotations

    def _get_mime_type(self) -> str:
        """Get MIME type from format or guess from file extension."""
        if self._format:
            fmt = self._format.lower()
            # Map common text formats to text/plain
            if fmt in {"plain", "txt", "text"}:
                return "text/plain"
            return f"application/{fmt}"

        if self.path:
            mime_type, _ = mimetypes.guess_type(self.path)
            if mime_type:
                return mime_type

        return "application/octet-stream"

    def to_resource_content(
        self,
        mime_type: str | None = None,
        annotations: Annotations | None = None,
    ) -> mcp.types.EmbeddedResource:
        if self.path:
            with open(self.path, "rb") as f:
                raw_data = f.read()
                uri_str = self.path.resolve().as_uri()
        elif self.data is not None:
            raw_data = self.data
            if self._name:
                uri_str = f"file:///{self._name}.{self._mime_type.split('/')[1]}"
            else:
                uri_str = f"file:///resource.{self._mime_type.split('/')[1]}"
        else:
            raise ValueError("No resource data available")

        mime = mime_type or self._mime_type
        UriType = Annotated[AnyUrl, UrlConstraints(host_required=False)]
        uri = TypeAdapter(UriType).validate_python(uri_str)

        if mime.startswith("text/"):
            try:
                text = raw_data.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_data.decode("latin-1")
            resource = mcp.types.TextResourceContents(
                text=text,
                mimeType=mime,
                uri=uri,
            )
        else:
            data = base64.b64encode(raw_data).decode()
            resource = mcp.types.BlobResourceContents(
                blob=data,
                mimeType=mime,
                uri=uri,
            )

        return mcp.types.EmbeddedResource(
            type="resource",
            resource=resource,
            annotations=annotations or self.annotations,
        )


def replace_type(type_, type_map: dict[type, type]):
    """
    Given a (possibly generic, nested, or otherwise complex) type, replaces all
    instances of old_type with new_type.

    This is useful for transforming types when creating tools.

    Args:
        type_: The type to replace instances of old_type with new_type.
        old_type: The type to replace.
        new_type: The type to replace old_type with.

    Examples:
    ```python
    >>> replace_type(list[int | bool], {int: str})
    list[str | bool]

    >>> replace_type(list[list[int]], {int: str})
    list[list[str]]
    ```
    """
    if type_ in type_map:
        return type_map[type_]

    origin = get_origin(type_)
    if not origin:
        return type_

    args = get_args(type_)
    new_args = tuple(replace_type(arg, type_map) for arg in args)

    if origin is UnionType:
        return Union[new_args]  # type: ignore # noqa: UP007
    else:
        return origin[new_args]
