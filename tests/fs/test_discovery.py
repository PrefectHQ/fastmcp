"""Tests for filesystem discovery module."""

import sys
from pathlib import Path

from fastmcp.prompts.base import Prompt
from fastmcp.resources.base import Resource
from fastmcp.resources.template import FunctionResourceTemplate, ResourceTemplate
from fastmcp.server.providers.filesystem_discovery import (
    discover_and_import,
    discover_files,
    extract_components,
    import_module_from_file,
)
from fastmcp.tools import FunctionTool
from fastmcp.tools.base import Tool


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
        """Should return empty list for module with no components."""
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
        """Should extract Tool objects."""
        py_file = tmp_path / "tools.py"
        py_file.write_text(
            """\
from fastmcp.tools import tool

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        assert len(components) == 1
        component = components[0]
        assert isinstance(component, FunctionTool)
        assert component.name == "greet"

    def test_extract_multiple_components(self, tmp_path: Path):
        """Should extract multiple component types."""
        py_file = tmp_path / "multi.py"
        py_file.write_text(
            """\
from fastmcp.tools import tool
from fastmcp.resources import resource
from fastmcp.prompts import prompt

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"

@resource("config://app")
def get_config() -> dict:
    return {}

@prompt
def analyze(topic: str) -> str:
    return f"Analyze: {topic}"
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        assert len(components) == 3
        types = {type(c).__name__ for c in components}
        assert types == {"FunctionTool", "FunctionResource", "FunctionPrompt"}

    def test_extract_skips_private_components(self, tmp_path: Path):
        """Should skip private components (those starting with _)."""
        py_file = tmp_path / "private.py"
        py_file.write_text(
            """\
from fastmcp.tools import tool

@tool
def public_tool() -> str:
    return "public"

# The module attribute starts with _, so it's skipped during discovery
@tool("private_tool_name")
def _private_tool() -> str:
    return "private"
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        # Only public_tool should be found (_private_tool starts with _, so skipped)
        assert len(components) == 1
        component = components[0]
        assert component.name == "public_tool"

    def test_extract_resource_template(self, tmp_path: Path):
        """Should extract ResourceTemplate objects."""
        py_file = tmp_path / "templates.py"
        py_file.write_text(
            """\
from fastmcp.resources import resource

@resource("users://{user_id}/profile")
def get_profile(user_id: str) -> dict:
    return {"id": user_id}
"""
        )

        module = import_module_from_file(py_file)
        components = extract_components(module)

        assert len(components) == 1
        component = components[0]
        assert isinstance(component, FunctionResourceTemplate)
        assert component.uri_template == "users://{user_id}/profile"


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
from fastmcp.tools import tool

@tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
        )

        result = discover_and_import(tmp_path)

        assert len(result.components) == 1
        file_path, component = result.components[0]
        assert file_path.name == "greet.py"
        assert isinstance(component, FunctionTool)
        assert component.name == "greet"

    def test_discover_and_import_skips_bad_imports(self, tmp_path: Path):
        """Should skip files that fail to import and track them."""
        (tmp_path / "good.py").write_text(
            """\
from fastmcp.tools import tool

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
        _, component = result.components[0]
        assert component.name == "good_tool"

        # bad.py should be in failed_files
        assert len(result.failed_files) == 1
        failed_path = tmp_path / "bad.py"
        assert failed_path in result.failed_files
        assert "nonexistent_module_xyz123" in result.failed_files[failed_path]


class TestExtractComponentsVersion:
    """Tests for version propagation in extract_components."""

    def test_extract_tool_preserves_version(self, tmp_path: Path):
        """Tools discovered from files should have their version attribute set."""
        tool_file = tmp_path / "versioned_tool.py"
        tool_file.write_text(
            """\
from fastmcp.tools import tool

@tool(version="1.0", description="v1")
def greet_v1(name: str) -> str:
    return f"Hi {name}"

@tool(version="2.0", description="v2")
def greet_v2(name: str) -> str:
    return f"Hey {name}"
"""
        )

        module = import_module_from_file(tool_file)
        components = extract_components(module)

        tools = [c for c in components if isinstance(c, Tool)]
        assert len(tools) == 2

        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0"}

    def test_extract_resource_preserves_version(self, tmp_path: Path):
        """Resources discovered from files should have their version attribute set."""
        resource_file = tmp_path / "versioned_resource.py"
        resource_file.write_text(
            """\
from fastmcp.resources import resource

@resource("data://config", version="1.0", name="config", description="v1 config")
def config_v1() -> str:
    return '{"theme": "light"}'
"""
        )

        module = import_module_from_file(resource_file)
        components = extract_components(module)

        resources = [c for c in components if isinstance(c, Resource)]
        assert len(resources) == 1
        assert resources[0].version == "1.0"

    def test_extract_resource_template_preserves_version(self, tmp_path: Path):
        """Resource templates discovered from files should have their version set."""
        template_file = tmp_path / "versioned_template.py"
        template_file.write_text(
            """\
from fastmcp.resources import resource

@resource("users://{user_id}/profile", version="2.0", description="v2 profile")
def get_profile(user_id: str) -> dict:
    return {"id": user_id}
"""
        )

        module = import_module_from_file(template_file)
        components = extract_components(module)

        templates = [c for c in components if isinstance(c, ResourceTemplate)]
        assert len(templates) == 1
        assert templates[0].version == "2.0"

    def test_extract_prompt_preserves_version(self, tmp_path: Path):
        """Prompts discovered from files should have their version attribute set."""
        prompt_file = tmp_path / "versioned_prompt.py"
        prompt_file.write_text(
            """\
from fastmcp.prompts import prompt

@prompt(name="summarize", version="1.0", description="v1 prompt")
def summarize_v1(text: str) -> str:
    return f"Summarize: {text}"
"""
        )

        module = import_module_from_file(prompt_file)
        components = extract_components(module)

        prompts = [c for c in components if isinstance(c, Prompt)]
        assert len(prompts) == 1
        assert prompts[0].version == "1.0"

    def test_discovered_tool_meta_includes_version(self, tmp_path: Path):
        """get_meta() should include version for tools discovered via filesystem."""
        tool_file = tmp_path / "meta_tool.py"
        tool_file.write_text(
            """\
from fastmcp.tools import tool

@tool(name="echo", version="3.0", description="Echo tool")
def echo(msg: str) -> str:
    return msg
"""
        )

        module = import_module_from_file(tool_file)
        components = extract_components(module)

        tool = components[0]
        meta = tool.get_meta()
        assert meta["fastmcp"]["version"] == "3.0"

    def test_unversioned_components_have_no_version(self, tmp_path: Path):
        """Components without version should have version=None."""
        tool_file = tmp_path / "no_version_tool.py"
        tool_file.write_text(
            """\
from fastmcp.tools import tool

@tool(description="No version")
def my_tool(x: str) -> str:
    return x
"""
        )

        module = import_module_from_file(tool_file)
        components = extract_components(module)

        assert len(components) == 1
        assert components[0].version is None
        meta = components[0].get_meta()
        assert "version" not in meta["fastmcp"]


class TestImportMachineryFixes:
    """Tests for import machinery correctness: sys.path cleanup, sys.modules safety, package root boundary."""

    def test_syspath_not_polluted_after_import(self, tmp_path: Path):
        """sys.path should not contain the file's parent after import_module_from_file returns."""
        (tmp_path / "mymod.py").write_text("VALUE = 1")
        path_before = list(sys.path)
        import_module_from_file(tmp_path / "mymod.py")
        assert sys.path == path_before

    def test_syspath_not_polluted_after_package_import(self, tmp_path: Path):
        """sys.path should not contain the package root's parent after a package import."""
        pkg = tmp_path / "mypkg_syspath"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text("VALUE = 2")
        path_before = list(sys.path)
        import_module_from_file(pkg / "mod.py", provider_root=tmp_path)
        assert sys.path == path_before

    def test_stdlib_not_shadowed_by_same_named_file(self, tmp_path: Path):
        """A provider file named json.py must not overwrite sys.modules['json']."""
        import json as stdlib_json

        saved = sys.modules["json"]
        try:
            (tmp_path / "json.py").write_text(
                "from fastmcp.tools import tool\n@tool\ndef parse(): return 'provider'"
            )
            import_module_from_file(tmp_path / "json.py")
            assert sys.modules.get("json") is stdlib_json
        finally:
            sys.modules["json"] = saved

    def test_same_stem_files_get_independent_modules(self, tmp_path: Path):
        """Two files with the same stem in different directories must not collide in sys.modules.

        The first-imported file keeps the bare stem key; the second gets a private key.
        Both modules must be independently accessible with correct content.
        """
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "helpers.py").write_text("ORIGIN = 'a'")
        (dir_b / "helpers.py").write_text("ORIGIN = 'b'")

        mod_a = import_module_from_file(dir_a / "helpers.py")
        mod_b = import_module_from_file(dir_b / "helpers.py")

        assert mod_a.ORIGIN == "a"
        assert mod_b.ORIGIN == "b"
        # The first module retains the bare stem key; the second uses a private key.
        # They must be distinct objects — the second import must not have clobbered the first.
        assert mod_a is not mod_b
        assert sys.modules.get("helpers") is not mod_b

    def test_package_root_bounded_by_provider_root(self, tmp_path: Path):
        """When the provider root is nested inside a larger package, import_module_from_file
        with provider_root must not escape into ancestor packages.

        The generated module name should be relative to the provider root (e.g. "myprovider.tools"),
        not to an ancestor package (e.g. "myproject.myprovider.tools"), and tmp_path (the
        ancestor's parent) must not be added to sys.path.
        """
        # Use a name that won't collide with any installed package
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "__init__.py").write_text("")
        provider = project / "myprovider"
        provider.mkdir()
        (provider / "__init__.py").write_text("")
        (provider / "tools.py").write_text("VALUE = 42")

        path_before = set(sys.path)
        mod = import_module_from_file(provider / "tools.py", provider_root=provider)
        path_after = set(sys.path)

        # Module was correctly imported
        assert mod.VALUE == 42
        # sys.path should not contain tmp_path (the ancestor's grandparent);
        # that would only happen if the package root escaped past the provider boundary
        assert str(tmp_path) not in (path_after - path_before)
        # The module name is bounded to the provider root, not "myproject.myprovider.tools"
        assert mod.__name__ == "myprovider.tools"

    def test_non_package_reload_returns_updated_content(self, tmp_path: Path):
        """Re-importing a non-package file should reflect file changes (exec_module path)."""
        f = tmp_path / "reloadable_np.py"
        f.write_text("VALUE = 'original'")
        mod = import_module_from_file(f)
        assert mod.VALUE == "original"

        f.write_text("VALUE = 'updated'")
        mod2 = import_module_from_file(f)
        assert mod2.VALUE == "updated"
