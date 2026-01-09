"""File discovery and module import utilities for filesystem-based routing.

This module provides functions to:
1. Discover Python files in a directory tree
2. Import modules (as packages if __init__.py exists, else directly)
3. Extract decorated functions from imported modules
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from fastmcp.fs.decorators import (
    FSMeta,
    get_fs_meta,
)
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DiscoveryResult:
    """Result of filesystem discovery."""

    components: list[tuple[Path, Any, FSMeta]] = field(default_factory=list)
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


def _find_package_root(file_path: Path) -> Path | None:
    """Find the root of the package containing this file.

    Walks up the directory tree until we find a directory without __init__.py.

    Returns:
        The package root directory, or None if not in a package.
    """
    current = file_path.parent
    package_root = None

    while current != current.parent:  # Stop at filesystem root
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


def import_module_from_file(file_path: Path) -> ModuleType:
    """Import a Python file as a module.

    If the file is part of a package (directory has __init__.py), imports
    it as a proper package member (relative imports work). Otherwise,
    imports directly using spec_from_file_location.

    Args:
        file_path: Path to the Python file.

    Returns:
        The imported module.

    Raises:
        ImportError: If the module cannot be imported.
    """
    file_path = file_path.resolve()

    # Check if this file is part of a package
    package_root = _find_package_root(file_path)

    if package_root is not None:
        # Import as part of a package
        module_name = _compute_module_name(file_path, package_root)

        # Ensure package root's parent is in sys.path
        package_parent = str(package_root.parent)
        if package_parent not in sys.path:
            sys.path.insert(0, package_parent)

        # Import using standard import machinery
        # If already imported, reload to pick up changes (for reload mode)
        try:
            if module_name in sys.modules:
                return importlib.reload(sys.modules[module_name])
            return importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(
                f"Failed to import {module_name} from {file_path}: {e}"
            ) from e
    else:
        # Import directly using spec_from_file_location
        module_name = file_path.stem

        # Ensure parent directory is in sys.path for imports
        parent_dir = str(file_path.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec for {file_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # Clean up sys.modules on failure
            sys.modules.pop(module_name, None)
            raise ImportError(f"Failed to execute module {file_path}: {e}") from e

        return module


def extract_components(module: ModuleType) -> list[tuple[Any, FSMeta]]:
    """Extract all decorated functions from a module.

    Scans all module attributes for functions that have been decorated
    with @tool, @resource, or @prompt.

    Args:
        module: The imported module to scan.

    Returns:
        List of (function, metadata) tuples for each decorated function.
    """
    components: list[tuple[Any, FSMeta]] = []

    for name in dir(module):
        # Skip private/magic attributes
        if name.startswith("_"):
            continue

        try:
            obj = getattr(module, name)
        except AttributeError:
            continue

        # Check if this object has our marker
        meta = get_fs_meta(obj)
        if meta is not None:
            components.append((obj, meta))

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
        Files with no decorated functions are silently skipped.
    """
    result = DiscoveryResult()

    for file_path in discover_files(root):
        try:
            module = import_module_from_file(file_path)
        except ImportError as e:
            result.failed_files[file_path] = str(e)
            continue
        except Exception as e:
            result.failed_files[file_path] = str(e)
            continue

        components = extract_components(module)
        for func, meta in components:
            result.components.append((file_path, func, meta))

    return result
