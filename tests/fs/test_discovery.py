"""Tests for fastmcp.fs discovery module."""

from pathlib import Path

from fastmcp.fs.decorators import ToolMeta
from fastmcp.fs.discovery import (
    discover_and_import,
    discover_files,
    extract_components,
    import_module_from_file,
)


class TestDiscoverFiles:
    """Tests for discover_files function."""

    def test_discover_files_empty_dir(self, tmp_path: Path):
        """Should return empty list for empty directory."""
        files = discover_files(tmp_path)
        assert files == []

    def test_discover_files_nonexistent_dir(self, tmp_path: Path):
        """Should return empty list for nonexistent directory."""
        nonexistent = tmp_path / "does_not_exist"
        files = discover_files(nonexistent)
        assert files == []

    def test_discover_files_single_file(self, tmp_path: Path):
        """Should find a single Python file."""
        py_file = tmp_path / "test.py"
        py_file.write_text("# test")

        files = discover_files(tmp_path)
        assert files == [py_file]

    def test_discover_files_skips_init(self, tmp_path: Path):
        """Should skip __init__.py files."""
        init_file = tmp_path / "__init__.py"
        init_file.write_text("# init")
        py_file = tmp_path / "test.py"
        py_file.write_text("# test")

        files = discover_files(tmp_path)
        assert files == [py_file]

    def test_discover_files_recursive(self, tmp_path: Path):
        """Should find files in subdirectories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        file1 = tmp_path / "a.py"
        file2 = subdir / "b.py"
        file1.write_text("# a")
        file2.write_text("# b")

        files = discover_files(tmp_path)
        assert sorted(files) == sorted([file1, file2])

    def test_discover_files_skips_pycache(self, tmp_path: Path):
        """Should skip __pycache__ directories."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        cache_file = pycache / "test.py"
        cache_file.write_text("# cache")
        py_file = tmp_path / "test.py"
        py_file.write_text("# test")

        files = discover_files(tmp_path)
        assert files == [py_file]

    def test_discover_files_sorted(self, tmp_path: Path):
        """Files should be returned in sorted order."""
        (tmp_path / "z.py").write_text("# z")
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "m.py").write_text("# m")

        files = discover_files(tmp_path)
        names = [f.name for f in files]
        assert names == ["a.py", "m.py", "z.py"]


class TestImportModuleFromFile:
    """Tests for import_module_from_file function."""

    def test_import_simple_module(self, tmp_path: Path):
        """Should import a simple module."""
        py_file = tmp_path / "simple.py"
        py_file.write_text("VALUE = 42")

        module = import_module_from_file(py_file)
        assert module.VALUE == 42

    def test_import_module_with_function(self, tmp_path: Path):
        """Should import a module with functions."""
        py_file = tmp_path / "funcs.py"
        py_file.write_text(
            """\
def greet(name):
    return f"Hello, {name}!"
"""
        )

        module = import_module_from_file(py_file)
        assert module.greet("World") == "Hello, World!"

    def test_import_module_with_imports(self, tmp_path: Path):
        """Should handle modules with standard library imports."""
        py_file = tmp_path / "with_imports.py"
        py_file.write_text(
            """\
import os
import sys

def get_cwd():
    return os.getcwd()
"""
        )

        module = import_module_from_file(py_file)
        assert callable(module.get_cwd)

    def test_import_as_package_with_init(self, tmp_path: Path):
        """Should import as package when __init__.py exists."""
        # Create package structure (use unique name to avoid module caching)
        pkg = tmp_path / "testpkg_init"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("PKG_VAR = 'package'")
        module_file = pkg / "module.py"
        module_file.write_text("MODULE_VAR = 'module'")

        module = import_module_from_file(module_file)
        assert module.MODULE_VAR == "module"

    def test_import_with_relative_import(self, tmp_path: Path):
        """Should support relative imports when in a package."""
        # Create package with relative import (use unique name to avoid module caching)
        pkg = tmp_path / "testpkg_relative"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "helper.py").write_text("HELPER_VALUE = 123")
        (pkg / "main.py").write_text(
            """\
from .helper import HELPER_VALUE

MAIN_VALUE = HELPER_VALUE * 2
"""
        )

        module = import_module_from_file(pkg / "main.py")
        assert module.MAIN_VALUE == 246

    def test_import_package_module_reload(self, tmp_path: Path):
        """Re-importing a package module should return updated content."""
        # Create package (use unique name to avoid conflicts)
        pkg = tmp_path / "testpkg_reload"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        module_file = pkg / "reloadable.py"
        module_file.write_text("VALUE = 'original'")

        # First import
        module = import_module_from_file(module_file)
        assert module.VALUE == "original"

        # Modify the file
        module_file.write_text("VALUE = 'updated'")

        # Re-import should see the updated value
        module = import_module_from_file(module_file)
        assert module.VALUE == "updated"


class TestExtractComponents:
    """Tests for extract_components function."""

    def test_extract_no_components(self, tmp_path: Path):
        """Should return empty list for module with no decorated functions."""
        py_file = tmp_path / "plain.py"
        py_file.write_text(
            """\
def plain_function():
    pass

SOME_VAR = 42
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)
        assert components == []

    def test_extract_tool_component(self, tmp_path: Path):
        """Should extract @tool decorated functions."""
        py_file = tmp_path / "tools.py"
        py_file.write_text(
            """\
from fastmcp.fs import tool

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        assert len(components) == 1
        func, meta = components[0]
        assert func.__name__ == "greet"
        assert isinstance(meta, ToolMeta)

    def test_extract_multiple_components(self, tmp_path: Path):
        """Should extract multiple decorated functions."""
        py_file = tmp_path / "multi.py"
        py_file.write_text(
            """\
from fastmcp.fs import tool, resource, prompt

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

@resource("config://app")
def get_config() -> dict:
    return {}

@prompt
def analyze(topic: str) -> list:
    return []
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        assert len(components) == 3
        names = {func.__name__ for func, _ in components}
        assert names == {"greet", "get_config", "analyze"}

    def test_extract_skips_private_functions(self, tmp_path: Path):
        """Should skip private functions even if decorated."""
        py_file = tmp_path / "private.py"
        py_file.write_text(
            """\
from fastmcp.fs import tool

@tool
def public_tool() -> str:
    return "public"

@tool
def _private_tool() -> str:
    return "private"
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        # Only public tool should be found (private starts with _)
        assert len(components) == 1
        func, _ = components[0]
        assert func.__name__ == "public_tool"


class TestDiscoverAndImport:
    """Tests for discover_and_import function."""

    def test_discover_and_import_empty(self, tmp_path: Path):
        """Should return empty result for empty directory."""
        result = discover_and_import(tmp_path)
        assert result.components == []
        assert result.failed_files == {}

    def test_discover_and_import_with_tools(self, tmp_path: Path):
        """Should discover and import tools."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "greet.py").write_text(
            """\
from fastmcp.fs import tool

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
        )

        result = discover_and_import(tmp_path)

        assert len(result.components) == 1
        file_path, func, meta = result.components[0]
        assert file_path.name == "greet.py"
        assert func.__name__ == "greet"
        assert isinstance(meta, ToolMeta)

    def test_discover_and_import_skips_bad_imports(self, tmp_path: Path):
        """Should skip files that fail to import and track them."""
        (tmp_path / "good.py").write_text(
            """\
from fastmcp.fs import tool

@tool
def good_tool() -> str:
    return "good"
"""
        )
        (tmp_path / "bad.py").write_text(
            """\
import nonexistent_module_xyz123

def bad_function():
    pass
"""
        )

        result = discover_and_import(tmp_path)

        # Only good.py should be imported
        assert len(result.components) == 1
        _, func, _ = result.components[0]
        assert func.__name__ == "good_tool"

        # bad.py should be in failed_files
        assert len(result.failed_files) == 1
        failed_path = tmp_path / "bad.py"
        assert failed_path in result.failed_files
        assert "nonexistent_module_xyz123" in result.failed_files[failed_path]
