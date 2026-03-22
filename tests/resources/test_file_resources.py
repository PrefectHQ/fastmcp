import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import FileUrl

from fastmcp.exceptions import ResourceError
from fastmcp.resources import FileResource
from fastmcp.resources.base import ResourceResult


@pytest.fixture
def temp_file():
    """Create a temporary file for testing.

    File is automatically cleaned up after the test if it still exists.
    """
    content = "test content"
    with NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(content)
        path = Path(f.name).resolve()
    yield path
    try:
        path.unlink()
    except FileNotFoundError:
        pass  # File was already deleted by the test


class TestFileResource:
    """Test FileResource functionality."""

    def test_file_resource_creation(self, temp_file: Path):
        """Test creating a FileResource."""
        resource = FileResource(
            uri=FileUrl(temp_file.as_uri()),
            name="test",
            description="test file",
            path=temp_file,
        )
        assert str(resource.uri) == temp_file.as_uri()
        assert resource.name == "test"
        assert resource.description == "test file"
        assert resource.mime_type == "text/plain"  # default
        assert resource.path == temp_file
        assert resource.is_binary is False  # default

    def test_file_resource_str_path_conversion(self, temp_file: Path):
        """Test FileResource handles string paths."""
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=Path(str(temp_file)),
        )
        assert isinstance(resource.path, Path)
        assert resource.path.is_absolute()

    async def test_read_text_file(self, temp_file: Path):
        """Test reading a text file."""
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
        )
        result = await resource.read()
        assert isinstance(result, ResourceResult)
        assert len(result.contents) == 1
        assert result.contents[0].content == "test content"
        assert result.contents[0].mime_type == "text/plain"

    async def test_read_binary_file(self, temp_file: Path):
        """Test reading a file as binary."""
        resource = FileResource(
            uri=FileUrl(f"file://{temp_file}"),
            name="test",
            path=temp_file,
            is_binary=True,
        )
        result = await resource.read()
        assert isinstance(result, ResourceResult)
        assert len(result.contents) == 1
        assert result.contents[0].content == b"test content"

    def test_relative_path_error(self):
        """Test error on relative path."""
        with pytest.raises(ValueError, match="Path must be absolute"):
            FileResource(
                uri=FileUrl("file:///test.txt"),
                name="test",
                path=Path("test.txt"),
            )

    async def test_missing_file_error(self, temp_file: Path):
        """Test error when file doesn't exist."""
        # Create path to non-existent file
        missing = temp_file.parent / "missing.txt"
        resource = FileResource(
            uri=FileUrl("file:///missing.txt"),
            name="test",
            path=missing,
        )
        with pytest.raises(ResourceError, match="Error reading file"):
            await resource.read()

    @pytest.mark.skipif(
        os.name == "nt" or (hasattr(os, "getuid") and os.getuid() == 0),
        reason="File permissions behave differently on Windows or when running as root",
    )
    async def test_permission_error(self, temp_file: Path):
        """Test reading a file without permissions."""
        temp_file.chmod(0o000)  # Remove all permissions
        try:
            resource = FileResource(
                uri=FileUrl(temp_file.as_uri()),
                name="test",
                path=temp_file,
            )
            with pytest.raises(ResourceError, match="Error reading file"):
                await resource.read()
        finally:
            temp_file.chmod(0o644)  # Restore permissions

    async def test_read_utf8_with_encoding(self, tmp_path: Path):
        """FileResource should read UTF-8 files correctly when encoding is specified."""
        content = (
            "Smart quotes: \u201cleft\u201d and apostrophe\u2019s em-dash\u2014here"
        )
        file = tmp_path / "utf8_test.md"
        file.write_text(content, encoding="utf-8")

        resource = FileResource(
            uri=FileUrl("file:///test/utf8"),
            path=file,
            mime_type="text/markdown",
            encoding="utf-8",
        )
        result = await resource.read()
        assert result.contents[0].content == content

    async def test_default_encoding_is_utf8(self, tmp_path: Path):
        """FileResource defaults to UTF-8, reading non-ASCII without explicit encoding."""
        content = "Smart quotes: \u201cleft\u201d and em-dash\u2014here"
        file = tmp_path / "default_utf8_test.txt"
        file.write_text(content, encoding="utf-8")

        resource = FileResource(
            uri=FileUrl("file:///test/default"),
            path=file,
        )
        assert resource.encoding == "utf-8"
        result = await resource.read()
        assert result.contents[0].content == content

    async def test_encoding_ignored_for_binary(self, tmp_path: Path):
        """Encoding field should be ignored when is_binary=True."""
        data = b"\x00\x01\x02\xff"
        file = tmp_path / "binary_test.bin"
        file.write_bytes(data)

        resource = FileResource(
            uri=FileUrl("file:///test/binary"),
            path=file,
            mime_type="application/octet-stream",
            encoding="utf-8",
        )
        result = await resource.read()
        assert result.contents[0].content == data

    async def test_read_latin1_with_encoding(self, tmp_path: Path):
        """FileResource should read non-UTF-8 files when correct encoding is specified."""
        content = "na\u00efve"
        file = tmp_path / "latin1_test.txt"
        file.write_text(content, encoding="latin-1")

        resource = FileResource(
            uri=FileUrl("file:///test/latin1"),
            path=file,
            encoding="latin-1",
        )
        result = await resource.read()
        assert result.contents[0].content == content
