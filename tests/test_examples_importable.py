"""Import-resolution gate for the ``examples/`` directory.

The example scripts sit outside the ty and pytest import gates, so stale
SDK idioms (renamed modules, moved symbols, v1 import paths) shipped there
repeatedly without CI noticing. This test closes that gap: it discovers every
``.py`` under ``examples/`` and asserts, WITHOUT executing any of them, that

1. the file parses as valid Python (AST), and
2. every top-level ``fastmcp`` / ``mcp`` / ``mcp_types`` import resolves
   against the installed package — both the module path and the imported
   names. This catches the recurring ``from mcp.types import X`` regression
   (the module is now ``mcp_types``) as well as renamed or moved symbols.

Execution is deliberately avoided: examples spin up servers, hit external
services, and pull heavy optional dependencies. Static resolution catches the
class of breakage we actually keep reintroducing (renamed imports) cheaply.

Standalone example sub-projects that pin their own ``fastmcp``/``mcp`` in a
local ``pyproject.toml`` (e.g. ``examples/testing_demo`` targets v1 on purpose)
are excluded — their imports are validated against a different package than the
one installed here.

Run:
    uv run pytest tests/test_examples_importable.py -v -s
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

# Snapshot baseline — ratchet DOWN as examples are fixed, never up.
MAX_IMPORT_FAILURES = 0

_CHECKED_ROOTS = ("fastmcp", "mcp", "mcp_types")


def _standalone_dirs() -> set[Path]:
    """Directories that are self-contained example sub-projects.

    A ``pyproject.toml`` under ``examples/`` marks a project boundary: the
    directory ships its own dependency pins (``examples/testing_demo`` targets
    fastmcp v1 on purpose, ``examples/smart_home`` pins fastmcp from git), so
    its imports must not be validated against the package installed for the
    main test suite. Everything below such a directory is excluded.
    """
    return {pyproject.parent for pyproject in EXAMPLES_DIR.rglob("pyproject.toml")}


def _find_example_files() -> list[Path]:
    standalone = _standalone_dirs()

    def is_standalone(path: Path) -> bool:
        return any(root in path.parents for root in standalone)

    return sorted(p for p in EXAMPLES_DIR.rglob("*.py") if not is_standalone(p))


def _check_imports(path: Path) -> list[str]:
    """Return descriptions of unresolved fastmcp/mcp_types imports."""
    try:
        tree = ast.parse(path.read_text("utf-8"))
    except SyntaxError as e:
        rel = path.relative_to(EXAMPLES_DIR.parent)
        return [f"{rel}:{e.lineno}: syntax error: {e.msg}"]

    errors: list[str] = []
    rel = path.relative_to(EXAMPLES_DIR.parent)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _CHECKED_ROOTS:
                    if _import_module(alias.name) is None:
                        errors.append(
                            f"{rel}:{node.lineno}: cannot import '{alias.name}'"
                        )
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — not a fastmcp/mcp_types path
                continue
            if node.module and node.module.split(".")[0] in _CHECKED_ROOTS:
                mod = _import_module(node.module)
                if mod is None:
                    errors.append(
                        f"{rel}:{node.lineno}: cannot import module '{node.module}'"
                    )
                    continue
                for alias in node.names:
                    name = alias.name
                    if name == "*":
                        continue
                    # ``from pkg import sub`` where ``sub`` is itself a
                    # submodule resolves even though ``pkg`` has no such
                    # attribute until the submodule is imported.
                    if hasattr(mod, name) or _import_module(f"{node.module}.{name}"):
                        continue
                    errors.append(
                        f"{rel}:{node.lineno}: '{node.module}' has no '{name}'"
                    )
    return errors


def _import_module(name: str):
    """Import ``name``, returning the module or None if it cannot be imported."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def test_examples_imports_resolve():
    """Example scripts must not regress in fastmcp/mcp_types import resolution.

    Parses every ``.py`` under ``examples/`` (excluding standalone sub-projects
    that pin their own fastmcp/mcp) and verifies its top-level fastmcp and
    mcp_types imports resolve against the installed package. Nothing is
    executed.
    """
    files = _find_example_files()
    assert files, "no example files discovered — check EXAMPLES_DIR"

    import_failures: list[str] = []
    for path in files:
        import_failures.extend(_check_imports(path))

    print(f"\nExample files checked: {len(files)}")
    print(f"Import failures:       {len(import_failures)}")
    if import_failures:
        print("\nUnresolved imports:")
        for failure in import_failures:
            print(f"  {failure}")

    assert len(import_failures) <= MAX_IMPORT_FAILURES, (
        f"Import failures regressed: {len(import_failures)} > {MAX_IMPORT_FAILURES}"
    )
