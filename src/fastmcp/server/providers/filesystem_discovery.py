"""File discovery and module import utilities for filesystem-based routing.

This module provides functions to:
1. Discover Python files in a directory tree
2. Import modules (as packages if __init__.py exists, else directly)
3. Extract decorated components (Tool, Resource, Prompt objects) from imported modules
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DiscoveryResult:
    """Result of filesystem discovery."""

    # Components are real objects (Tool, Resource, ResourceTemplate, Prompt)
    components: list[tuple[Path, FastMCPComponent]] = field(default_factory=list)
    failed_files: dict[Path, str] = field(default_factory=dict)  # path -> error message


def discover_files(root: Path) -> list[Path]:
    """Recursively discover all Python files under a directory.

    Excludes __init__.py files (they're for package structure, not components).

    Args:
        root: Root directory to scan.

    Returns:
        List of .py file paths, sorted for deterministic order.
    """
    if not root.exists():
        return []

    if not root.is_dir():
        # If root is a file, just return it (if it's a .py file)
        if root.suffix == ".py" and root.name != "__init__.py":
            return [root]
        return []

    files: list[Path] = []
    for path in root.rglob("*.py"):
        # Skip __init__.py files
        if path.name == "__init__.py":
            continue
        # Skip __pycache__ directories
        if "__pycache__" in path.parts:
            continue
        files.append(path)

    # Sort for deterministic discovery order
    return sorted(files)


def _is_package_dir(directory: Path) -> bool:
    """Check if a directory is a Python package (has __init__.py)."""
    return (directory / "__init__.py").exists()


def _find_package_root(file_path: Path, stop_at: Path | None = None) -> Path | None:
    """Find the root of the package containing this file.

    Walks up the directory tree until we find a directory without __init__.py,
    but never above stop_at (the provider root). This prevents escaping into
    ancestor packages when the provider is nested inside a larger Python project.

    Args:
        file_path: Path to the Python file.
        stop_at: Do not walk above this directory. Typically the provider root.

    Returns:
        The package root directory, or None if not in a package.
    """
    current = file_path.parent
    package_root = None

    while current != current.parent:  # Stop at filesystem root
        if stop_at is not None and current == stop_at.parent:
            break  # Don't escape above the provider root
        if _is_package_dir(current):
            package_root = current
            current = current.parent
        else:
            break

    return package_root


def _compute_module_name(file_path: Path, package_root: Path) -> str:
    """Compute the dotted module name for a file within a package.

    Args:
        file_path: Path to the Python file.
        package_root: Root directory of the package.

    Returns:
        Dotted module name (e.g., "mcp.tools.greet").
    """
    relative = file_path.relative_to(package_root.parent)
    parts = list(relative.parts)
    # Remove .py extension from last part
    parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def import_module_from_file(
    file_path: Path, provider_root: Path | None = None
) -> ModuleType:
    """Import a Python file as a module.

    If the file is part of a package (directory has __init__.py), imports
    it as a proper package member (relative imports work). Otherwise,
    imports directly using spec_from_file_location.

    sys.path is modified only for the duration of the import and restored
    immediately after, so no permanent pollution occurs.

    Args:
        file_path: Path to the Python file.
        provider_root: The provider's root directory. Prevents package root
            discovery from walking above this boundary into ancestor packages.

    Returns:
        The imported module.

    Raises:
        ImportError: If the module cannot be imported.
    """
    file_path = file_path.resolve()
    if provider_root is not None:
        provider_root = provider_root.resolve()

    # Check if this file is part of a package
    package_root = _find_package_root(file_path, stop_at=provider_root)

    if package_root is not None:
        # Import as part of a package
        module_name = _compute_module_name(file_path, package_root)

        # Temporarily add package root's parent to sys.path for the import
        package_parent = str(package_root.parent)
        path_added = package_parent not in sys.path
        if path_added:
            sys.path.insert(0, package_parent)

        try:
            # If already imported, reload to pick up changes (for reload mode)
            if module_name in sys.modules:
                return importlib.reload(sys.modules[module_name])
            return importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(
                f"Failed to import {module_name} from {file_path}: {e}"
            ) from e
        finally:
            if path_added:
                with contextlib.suppress(ValueError):
                    sys.path.remove(package_parent)
    else:
        # Import directly using spec_from_file_location
        stem = file_path.stem
        parent_dir = str(file_path.parent)

        # Determine the sys.modules key. Prefer the bare stem (so that sibling
        # imports like `import helpers` resolve correctly), but fall back to a
        # private collision-safe key if the bare stem is already claimed by
        # something else (stdlib, a third-party package, or another provider file
        # from a different directory).
        existing = sys.modules.get(stem)
        if existing is not None and getattr(existing, "__file__", None) != str(
            file_path
        ):
            module_name = f"_fastmcp_{stem}_{hashlib.sha1(str(file_path).encode()).hexdigest()[:12]}"
        else:
            module_name = stem

        # Temporarily add parent to sys.path so module-level sibling imports resolve.
        # Safe to remove after exec_module: all top-level imports are resolved by then,
        # and sibling files imported as side effects are already in sys.modules.
        path_added = parent_dir not in sys.path
        if path_added:
            sys.path.insert(0, parent_dir)

        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load spec for {file_path}")

            existing = sys.modules.get(module_name)
            if existing is not None:
                # Re-exec in place rather than importlib.reload: reload() re-finds
                # the module by name via sys.path, which fails for private keys
                # (the file is tool.py, not _fastmcp_tool_xxx.py).
                existing.__spec__ = spec
                existing.__loader__ = spec.loader
                existing.__file__ = str(file_path)
                try:
                    spec.loader.exec_module(existing)
                except Exception as e:
                    raise ImportError(
                        f"Failed to reload module {file_path}: {e}"
                    ) from e
                return existing

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module

            try:
                spec.loader.exec_module(module)
            except Exception as e:
                # Clean up sys.modules on failure
                sys.modules.pop(module_name, None)
                raise ImportError(f"Failed to execute module {file_path}: {e}") from e

            return module
        finally:
            if path_added:
                with contextlib.suppress(ValueError):
                    sys.path.remove(parent_dir)


def extract_components(module: ModuleType) -> list[FastMCPComponent]:
    """Extract all MCP components from a module.

    Scans all module attributes for instances of Tool, Resource,
    ResourceTemplate, or Prompt objects created by standalone decorators,
    or functions decorated with @tool/@resource/@prompt that have __fastmcp__ metadata.

    Args:
        module: The imported module to scan.

    Returns:
        List of component objects (Tool, Resource, ResourceTemplate, Prompt).
    """
    # Import here to avoid circular imports
    import inspect

    from fastmcp.decorators import get_fastmcp_meta
    from fastmcp.prompts.base import Prompt
    from fastmcp.prompts.function_prompt import PromptMeta
    from fastmcp.resources.base import Resource
    from fastmcp.resources.function_resource import ResourceMeta
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.dependencies import without_injected_parameters
    from fastmcp.tools.base import Tool
    from fastmcp.tools.function_tool import ToolMeta

    component_types = (Tool, Resource, ResourceTemplate, Prompt)
    components: list[FastMCPComponent] = []

    for name in dir(module):
        # Skip private/magic attributes
        if name.startswith("_"):
            continue

        try:
            obj = getattr(module, name)
        except AttributeError:
            continue

        # Check if this object is a component type
        if isinstance(obj, component_types):
            components.append(obj)
            continue

        # Check for functions with __fastmcp__ metadata
        meta = get_fastmcp_meta(obj)
        if meta is not None:
            if isinstance(meta, ToolMeta):
                resolved_task = meta.task if meta.task is not None else False
                tool = Tool.from_function(
                    obj,
                    name=meta.name,
                    version=meta.version,
                    title=meta.title,
                    description=meta.description,
                    icons=meta.icons,
                    tags=meta.tags,
                    output_schema=meta.output_schema,
                    annotations=meta.annotations,
                    meta=meta.meta,
                    task=resolved_task,
                    exclude_args=meta.exclude_args,
                    serializer=meta.serializer,
                    auth=meta.auth,
                    run_in_thread=meta.run_in_thread,
                )
                components.append(tool)
            elif isinstance(meta, ResourceMeta):
                resolved_task = meta.task if meta.task is not None else False
                has_uri_params = "{" in meta.uri and "}" in meta.uri
                wrapper_fn = without_injected_parameters(obj)
                has_func_params = bool(inspect.signature(wrapper_fn).parameters)

                if has_uri_params or has_func_params:
                    resource = ResourceTemplate.from_function(
                        fn=obj,
                        uri_template=meta.uri,
                        name=meta.name,
                        version=meta.version,
                        title=meta.title,
                        description=meta.description,
                        icons=meta.icons,
                        mime_type=meta.mime_type,
                        tags=meta.tags,
                        annotations=meta.annotations,
                        meta=meta.meta,
                        task=resolved_task,
                        auth=meta.auth,
                    )
                else:
                    resource = Resource.from_function(
                        fn=obj,
                        uri=meta.uri,
                        name=meta.name,
                        version=meta.version,
                        title=meta.title,
                        description=meta.description,
                        icons=meta.icons,
                        mime_type=meta.mime_type,
                        tags=meta.tags,
                        annotations=meta.annotations,
                        meta=meta.meta,
                        task=resolved_task,
                        auth=meta.auth,
                    )
                components.append(resource)
            elif isinstance(meta, PromptMeta):
                resolved_task = meta.task if meta.task is not None else False
                prompt = Prompt.from_function(
                    obj,
                    name=meta.name,
                    version=meta.version,
                    title=meta.title,
                    description=meta.description,
                    icons=meta.icons,
                    tags=meta.tags,
                    meta=meta.meta,
                    task=resolved_task,
                    auth=meta.auth,
                )
                components.append(prompt)

    return components


def discover_and_import(root: Path) -> DiscoveryResult:
    """Discover files, import modules, and extract components.

    This is the main entry point for filesystem-based discovery.

    Args:
        root: Root directory to scan.

    Returns:
        DiscoveryResult with components and any failed files.

    Note:
        Files that fail to import are tracked in failed_files, not logged.
        The caller is responsible for logging/handling failures.
        Files with no components are silently skipped.
    """
    result = DiscoveryResult()

    for file_path in discover_files(root):
        try:
            module = import_module_from_file(file_path, provider_root=root)
        except Exception as e:
            result.failed_files[file_path] = str(e)
            continue

        components = extract_components(module)
        for component in components:
            result.components.append((file_path, component))

    return result
