"""Concrete resource implementations."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pydantic.json
from anyio import Path as AsyncPath
from pydantic import Field, ValidationInfo
from typing_extensions import override

from fastmcp.exceptions import ResourceError
from fastmcp.resources.resource import Resource, ResourceContent, ResourceResult
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class TextResource(Resource):
    """A resource that reads from a string."""

    text: str = Field(description="Text content of the resource")

    async def read(self) -> ResourceResult:
        """Read the text content."""
        return ResourceResult(
            contents=[ResourceContent(content=self.text, mime_type=self.mime_type)]
        )


class BinaryResource(Resource):
    """A resource that reads from bytes."""

    data: bytes = Field(description="Binary content of the resource")

    async def read(self) -> ResourceResult:
        """Read the binary content."""
        return ResourceResult(
            contents=[ResourceContent(content=self.data, mime_type=self.mime_type)]
        )


class FileResource(Resource):
    """A resource that reads from a file.

    Set is_binary=True to read file as binary data instead of text.
    """

    path: Path = Field(description="Path to the file")
    is_binary: bool = Field(
        default=False,
        description="Whether to read the file as binary data",
    )
    mime_type: str = Field(
        default="text/plain",
        description="MIME type of the resource content",
    )

    @property
    def _async_path(self) -> AsyncPath:
        return AsyncPath(self.path)

    @pydantic.field_validator("path")
    @classmethod
    def validate_absolute_path(cls, path: Path) -> Path:
        """Ensure path is absolute."""
        if not path.is_absolute():
            raise ValueError("Path must be absolute")
        return path

    @pydantic.field_validator("is_binary")
    @classmethod
    def set_binary_from_mime_type(cls, is_binary: bool, info: ValidationInfo) -> bool:
        """Set is_binary based on mime_type if not explicitly set."""
        if is_binary:
            return True
        mime_type = info.data.get("mime_type", "text/plain")
        return not mime_type.startswith("text/")

    @override
    async def read(self) -> ResourceResult:
        """Read the file content."""
        try:
            if self.is_binary:
                content: str | bytes = await self._async_path.read_bytes()
            else:
                content = await self._async_path.read_text()
            return ResourceResult(
                contents=[ResourceContent(content=content, mime_type=self.mime_type)]
            )
        except Exception as e:
            raise ResourceError(f"Error reading file {self.path}") from e


class HttpResource(Resource):
    """A resource that reads from an HTTP endpoint."""

    url: str = Field(description="URL to fetch content from")
    mime_type: str = Field(
        default="application/json", description="MIME type of the resource content"
    )

    @override
    async def read(self) -> ResourceResult:
        """Read the HTTP content."""
        async with httpx.AsyncClient() as client:
            response = await client.get(self.url)
            _ = response.raise_for_status()
            return ResourceResult(
                contents=[
                    ResourceContent(content=response.text, mime_type=self.mime_type)
                ]
            )


class DirectoryResource(Resource):
    """A resource that lists files in a directory."""

    path: Path = Field(description="Path to the directory")
    recursive: bool = Field(
        default=False, description="Whether to list files recursively"
    )
    pattern: str | None = Field(
        default=None, description="Optional glob pattern to filter files"
    )
    mime_type: str = Field(
        default="application/json", description="MIME type of the resource content"
    )

    @property
    def _async_path(self) -> AsyncPath:
        return AsyncPath(self.path)

    @pydantic.field_validator("path")
    @classmethod
    def validate_absolute_path(cls, path: Path) -> Path:
        """Ensure path is absolute."""
        if not path.is_absolute():
            raise ValueError("Path must be absolute")
        return path

    async def list_files(self) -> list[Path]:
        """List files in the directory."""
        if not await self._async_path.exists():
            raise FileNotFoundError(f"Directory not found: {self.path}")
        if not await self._async_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {self.path}")

        pattern = self.pattern or "*"

        glob_fn = self._async_path.rglob if self.recursive else self._async_path.glob
        try:
            return [Path(p) async for p in glob_fn(pattern) if await p.is_file()]
        except Exception as e:
            raise ResourceError(f"Error listing directory {self.path}") from e

    @override
    async def read(self) -> ResourceResult:
        """Read the directory listing."""
        try:
            files: list[Path] = await self.list_files()

            file_list = [str(f.relative_to(self.path)) for f in files]

            content = json.dumps({"files": file_list}, indent=2)
            return ResourceResult(
                contents=[ResourceContent(content=content, mime_type=self.mime_type)]
            )
        except Exception as e:
            raise ResourceError(f"Error reading directory {self.path}") from e
