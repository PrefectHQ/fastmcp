"""Common types used across FastMCP."""

import base64
from pathlib import Path
from types import UnionType
from typing import Annotated, TypeVar, Union, get_args, get_origin

from mcp.types import ImageContent

T = TypeVar("T")


def issubclass_safe(cls: type, base: type) -> bool:
    """Check if cls is a subclass of base, even if cls is a type variable."""
    try:
        if origin := get_origin(cls):
            return issubclass_safe(origin, base)
        return issubclass(cls, base)
    except TypeError:
        return False


def is_class_member_of_type(cls: type, base: type) -> bool:
    """Check if cls is a member of base, even if cls is a type variable."""
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


def _convert_set_defaults(maybe_set: set[T] | list[T] | None) -> set[T]:
    """Convert a set or list to a set, defaulting to an empty set if None."""
    if maybe_set is None:
        return set()
    if isinstance(maybe_set, set):
        return maybe_set
    return set(maybe_set)


class Image:
    """Helper class for returning images from tools."""

    def __init__(
        self,
        path: str | Path | None = None,
        data: bytes | None = None,
        format: str | None = None,
    ):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        if path is not None and data is not None:
            raise ValueError("Only one of path or data can be provided")

        self.path = Path(path) if path else None
        self.data = data
        self._format = format
        self._mime_type = self._get_mime_type()

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

    def to_image_content(self) -> ImageContent:
        """Convert to MCP ImageContent."""
        if self.path:
            with open(self.path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
        elif self.data is not None:
            data = base64.b64encode(self.data).decode()
        else:
            raise ValueError("No image data available")

        return ImageContent(type="image", data=data, mimeType=self._mime_type)
