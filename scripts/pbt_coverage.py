"""Map how much of fastmcp the property-based tests exercise.

A test is property-based when its file imports hypothesis. The script runs only
those files under coverage.py with per-test contexts, then reports, for every
module in `fastmcp_slim/fastmcp`, how many statements inside function bodies ran
during a property-based test.

Function bodies are the denominator on purpose. Importing a module executes its
imports, `def` and `class` lines, and module-level assignments, so whole-file
line coverage credits any test that merely imports fastmcp. Lines executed
while pytest collected tests carry no test context and never count.

A module is public when the docs (excluding the generated SDK reference) import
from it, or when it defines a name the docs import. That is the surface users
are told to touch; everything else is reachable with `public: false`.
"""

import argparse
import ast
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "fastmcp-pbt-coverage/1"
ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "fastmcp_slim" / "fastmcp"
HYPOTHESIS_IMPORT = re.compile(r"^\s*(from|import)\s+hypothesis\b", re.MULTILINE)
DOC_IMPORT = re.compile(
    r"^[ \t]*from\s+(fastmcp(?:\.\w+)*)\s+import\s+(\([^)]*\)|[^\n]+)", re.MULTILINE
)
MAX_TESTS_LISTED = 8
NO_COVER = r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)"


def discover_pbt_tests(tests_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in tests_dir.rglob("*.py")
        if HYPOTHESIS_IMPORT.search(path.read_text(encoding="utf-8"))
    )


def run_suite(test_files: list[Path], data_file: Path) -> int:
    """Run the property-based tests with per-test coverage contexts."""
    env = os.environ | {"COVERAGE_FILE": str(data_file)}
    command = [
        sys.executable,
        "-m",
        "pytest",
        *map(str, test_files),
        "-p",
        "no:cacheprovider",
        "--cov=fastmcp",
        "--cov-context=test",
        "--cov-report=",
        "-q",
    ]
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def parse_doc_imports(docs_dir: Path) -> dict[str, set[str]]:
    """Map each fastmcp module the docs import from to the names they import."""
    imports: dict[str, set[str]] = defaultdict(set)
    for page in docs_dir.rglob("*.mdx"):
        if "python-sdk" in page.relative_to(docs_dir).parts:
            continue
        for match in DOC_IMPORT.finditer(page.read_text(encoding="utf-8")):
            names = match.group(2).split("#", 1)[0].strip("()\n ")
            for name in names.replace("\n", ",").split(","):
                name = name.strip().split(" as ")[0].strip()
                if name.isidentifier():
                    imports[match.group(1)].add(name)
    return imports


def resolve_public(
    doc_imports: dict[str, set[str]], modules: set[str]
) -> dict[str, set[str]]:
    """Map each public module to the documented names it defines.

    An empty set means the module is public as an import path (for example a
    package `__init__`) without defining a documented name itself.
    """
    public: dict[str, set[str]] = defaultdict(set)
    for source, names in doc_imports.items():
        if source in modules:
            public[source]
        try:
            imported = importlib.import_module(source)
        except Exception:  # optional extras may be missing; keep the path only
            continue
        for name in names:
            try:
                obj = getattr(imported, name)
            except Exception:
                continue
            if isinstance(obj, type(sys)):
                if obj.__name__ in modules:
                    public[obj.__name__]
                continue
            owner = getattr(obj, "__module__", None)
            if owner in modules:
                public[owner].add(getattr(obj, "__name__", name))
            elif source in modules:
                public[source].add(name)
    return public


def function_spans(tree: ast.Module) -> list[tuple[str, int, int]]:
    """Every function's qualified name and the line span of its body."""
    spans: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                start = child.body[0].lineno
                spans.append((qualname, start, child.end_lineno or start))
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def top_level_spans(tree: ast.Module) -> dict[str, tuple[str, int, int]]:
    spans = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            spans[node.name] = (kind, node.lineno, node.end_lineno or node.lineno)
    return spans


def test_id(context: str) -> str:
    return context.rsplit("|", 1)[0]


def analyze(
    data_file: Path | None,
    public: dict[str, set[str]],
    paths: list[Path] | None = None,
) -> tuple[list[dict], int]:
    data = None
    if data_file is not None and data_file.exists():
        from coverage import CoverageData

        data = CoverageData(basename=str(data_file))
        data.read()

    all_tests: set[str] = set()
    modules = []
    for path in paths if paths is not None else sorted(PACKAGE.rglob("*.py")):
        name = module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = function_spans(tree)
        body_lines = {
            line for _, start, end in functions for line in range(start, end + 1)
        }
        statements = _parse_statements(path) & body_lines

        hits: dict[int, set[str]] = {}
        if data is not None:
            by_line = data.contexts_by_lineno(str(path))
            for line, contexts in by_line.items():
                tests = {test_id(c) for c in contexts if c}
                if tests and line in statements:
                    hits[line] = tests
        reached_tests = set().union(*hits.values()) if hits else set()
        all_tests |= reached_tests

        def lines_in(start: int, end: int) -> tuple[int, int]:
            span = {line for line in statements if start <= line <= end}
            return len(span), len(span & hits.keys())

        functions_hit = 0
        for _, start, end in functions:
            total, hit = lines_in(start, end)
            functions_hit += bool(hit)

        symbols = []
        definitions = top_level_spans(tree)
        for symbol in sorted(public.get(name, ())):
            kind, start, end = definitions.get(symbol, ("value", 0, -1))
            total, hit = lines_in(start, end)
            symbols.append(
                {"name": symbol, "kind": kind, "statements": total, "hit": hit}
            )

        modules.append(
            {
                "module": name,
                "path": str(path.relative_to(ROOT)),
                "public": name in public,
                "statements": len(statements),
                "hit": len(hits),
                "functions": len(functions),
                "functions_hit": functions_hit,
                "symbols": symbols,
                "tests": len(reached_tests),
                "test_ids": sorted(reached_tests)[:MAX_TESTS_LISTED],
            }
        )
    return modules, len(all_tests)


def _parse_statements(path: Path) -> set[int]:
    from coverage.python import PythonParser

    parser = PythonParser(filename=str(path), exclude=NO_COVER)
    parser.parse_source()
    return set(parser.statements)


def source_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data",
        type=Path,
        help="Analyze an existing coverage data file instead of running the suite",
    )
    args = parser.parse_args()

    test_files = discover_pbt_tests(ROOT / "tests")
    exit_code = None
    data_file = args.data
    if data_file is None and test_files:
        data_file = Path(tempfile.mkdtemp()) / ".coverage"
        exit_code = run_suite(test_files, data_file)

    all_modules = {module_name(path) for path in PACKAGE.rglob("*.py")}
    public = resolve_public(parse_doc_imports(ROOT / "docs"), all_modules)
    modules, test_count = analyze(data_file, public)

    if not test_files and args.data is None:
        status = "no_suite"
    elif exit_code not in (None, 0):
        status = "failed"
    else:
        status = "passed"
    report = {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_commit": source_commit(),
        "suite": {
            "status": status,
            "pytest_exit_code": exit_code,
            "test_files": [str(p.relative_to(ROOT)) for p in test_files],
            "tests_reaching_fastmcp": test_count,
        },
        "modules": modules,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    covered = sum(m["hit"] for m in modules)
    total = sum(m["statements"] for m in modules)
    print(f"{status}: {covered}/{total} function-body statements hit by PBT")


if __name__ == "__main__":
    main()
