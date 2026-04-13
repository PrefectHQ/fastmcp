"""Validate Python code examples in FastMCP documentation.

Extracts code blocks from .mdx docs and checks:
1. Syntax — every example parses as valid Python
2. FastMCP imports — every ``from fastmcp.x import y`` resolves

Supports tags in code fence prefix (for future use):
    ```python test="skip"      — skip all checks
    ```python lint="skip"      — skip all checks

Run:
    uv run pytest tests/docs/test_doc_examples.py -v -s
"""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from pytest_examples import CodeExample
from pytest_examples.find_examples import _extract_code_chunks

DOCS_DIR = Path("docs")
_SKIP_DIRS = {"python-sdk", "public"}

# Snapshot baselines — ratchet DOWN as doc examples are fixed.
MAX_SYNTAX_FAILURES = 0
MAX_IMPORT_FAILURES = 0


def _find_mdx_examples() -> list[CodeExample]:
    """Find Python code examples in .mdx files.

    pytest-examples only supports ``.md``; we call its internal
    ``_extract_code_chunks`` directly so ``.mdx`` works without copying.
    """
    examples: list[CodeExample] = []
    for mdx_file in sorted(DOCS_DIR.rglob("*.mdx")):
        rel = mdx_file.relative_to(DOCS_DIR)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        code = mdx_file.read_text("utf-8")
        group = uuid4()
        examples.extend(_extract_code_chunks(mdx_file, code, group))
    return examples


def _should_skip(example: CodeExample) -> bool:
    settings = example.prefix_settings()
    return settings.get("lint") == "skip" or settings.get("test") == "skip"


def _check_syntax(example: CodeExample) -> str | None:
    """Return error description if syntax is invalid, else None."""
    try:
        ast.parse(example.source)
        return None
    except SyntaxError as e:
        rel = Path(example.path).relative_to(DOCS_DIR)
        return f"{rel}:{example.start_line}: line {e.lineno}: {e.msg}"


def _check_fastmcp_imports(example: CodeExample) -> list[str]:
    """Return list of broken fastmcp import descriptions."""
    try:
        tree = ast.parse(example.source)
    except SyntaxError:
        return []

    errors: list[str] = []
    rel = Path(example.path).relative_to(DOCS_DIR)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("fastmcp"):
                    try:
                        __import__(alias.name)
                    except ImportError:
                        errors.append(
                            f"{rel}:{example.start_line}: cannot import '{alias.name}'"
                        )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("fastmcp"):
                names = [a.name for a in node.names]
                try:
                    mod = __import__(node.module, fromlist=names)
                except ImportError:
                    errors.append(
                        f"{rel}:{example.start_line}: "
                        f"cannot import module '{node.module}'"
                    )
                    continue
                for name in names:
                    if not hasattr(mod, name):
                        errors.append(
                            f"{rel}:{example.start_line}: "
                            f"'{node.module}' has no '{name}'"
                        )
    return errors


def test_doc_examples_quality():
    """Doc examples should not regress in syntax or import correctness.

    Checks every Python code block in ``docs/*.mdx`` (excluding
    auto-generated ``python-sdk/`` and ``public/`` directories).
    Reports all failures and asserts counts don't exceed known baselines.
    """
    examples = _find_mdx_examples()
    syntax_failures: list[str] = []
    import_failures: list[str] = []

    for ex in examples:
        if _should_skip(ex):
            continue

        err = _check_syntax(ex)
        if err:
            syntax_failures.append(err)
            continue

        import_failures.extend(_check_fastmcp_imports(ex))

    total = len(examples)
    print(f"\nDoc examples checked: {total}")
    print(f"Syntax failures:     {len(syntax_failures)}")
    print(f"Import failures:     {len(import_failures)}")

    if syntax_failures:
        print("\nSyntax errors:")
        for f in syntax_failures:
            print(f"  {f}")

    if import_failures:
        print("\nBroken imports:")
        for f in import_failures:
            print(f"  {f}")

    assert len(syntax_failures) <= MAX_SYNTAX_FAILURES, (
        f"Syntax failures regressed: {len(syntax_failures)} > {MAX_SYNTAX_FAILURES}"
    )
    assert len(import_failures) <= MAX_IMPORT_FAILURES, (
        f"Import failures regressed: {len(import_failures)} > {MAX_IMPORT_FAILURES}"
    )
