"""What the property-based coverage map counts, and what it refuses to count."""

import ast
import importlib.util
from pathlib import Path

import pytest
from coverage import CoverageData

ROOT = Path(__file__).parents[1]


@pytest.fixture
def pbt():
    spec = importlib.util.spec_from_file_location(
        "pbt_coverage", ROOT / "scripts" / "pbt_coverage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_function_spans_cover_bodies_not_headers(pbt):
    source = (
        "import os\n"  # 1
        "X = 1\n"  # 2
        "def f():\n"  # 3
        "    return 1\n"  # 4
        "class C:\n"  # 5
        "    y = 2\n"  # 6
        "    @property\n"  # 7
        "    def g(self):\n"  # 8
        "        def h():\n"  # 9
        "            return 3\n"  # 10
        "        return h()\n"  # 11
    )
    spans = pbt.function_spans(ast.parse(source))
    assert spans == [("f", 4, 4), ("C.g", 9, 11), ("C.g.h", 10, 10)]


def test_function_spans_skip_bodies_that_never_run(pbt):
    source = (
        "from typing import TYPE_CHECKING, overload\n"
        "if TYPE_CHECKING:\n"
        "    def hint():\n"
        "        return 1\n"
        "else:\n"
        "    def real():\n"
        "        return 2\n"
        "@overload\n"
        "def f(x: int) -> int: ...\n"
        "def f(x):\n"
        "    return x\n"
        "class P:\n"
        "    def method(self):\n"
        '        """Protocol stub."""\n'
        "        ...\n"
    )
    spans = pbt.function_spans(ast.parse(source))
    assert [name for name, _, _ in spans] == ["real", "f"]


def test_doc_imports_handle_comments_aliases_and_skip_archived_docs(pbt, tmp_path):
    (tmp_path / "guide.mdx").write_text(
        "```python\n"
        "from fastmcp import FastMCP, Context as Ctx  # comment\n"
        "from fastmcp.server.auth import (\n"
        "    JWTVerifier,  # verifies tokens\n"
        "    RemoteAuthProvider,\n"
        ")\n"
        "from fastmcp.tools import *\n"
        "```\n"
    )
    for skipped in ("python-sdk", "v2"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "ref.mdx").write_text(
            "from fastmcp.utilities.cli import internal\n"
        )
    assert pbt.parse_doc_imports(tmp_path) == {
        "fastmcp": {"FastMCP", "Context"},
        "fastmcp.server.auth": {"JWTVerifier", "RemoteAuthProvider"},
    }


def test_public_names_resolve_to_their_defining_module(pbt):
    modules = {"fastmcp", "fastmcp.server.server", "fastmcp.server.context"}
    public = pbt.resolve_public({"fastmcp": {"FastMCP", "Context"}}, modules)
    assert public == {
        "fastmcp": set(),
        "fastmcp.server.server": {"FastMCP"},
        "fastmcp.server.context": {"Context"},
    }


def test_only_lines_run_inside_a_test_count(pbt, tmp_path):
    path = pbt.PACKAGE / "exceptions.py"
    tree = ast.parse(path.read_text())
    _, start, end = next(
        span for span in pbt.function_spans(tree) if span[0].endswith("__init__")
    )
    header = start - 1

    data_file = tmp_path / ".coverage"
    data = CoverageData(basename=str(data_file))
    data.set_context("")  # collection/import time
    data.add_lines({str(path): [header, start]})
    data.set_context("tests/pbt/test_x.py::test_prop|run")
    data.add_lines({str(path): [start]})
    data.write()

    modules, tests = pbt.analyze(data_file, {"fastmcp.exceptions": set()}, paths=[path])
    report = next(m for m in modules if m["module"] == "fastmcp.exceptions")
    assert report["hit"] == 1
    assert report["functions_hit"] == 1
    assert report["test_ids"] == ["tests/pbt/test_x.py::test_prop"]
    assert report["public"] is True
    assert tests == 1


def test_no_suite_reports_modules_unreached(pbt):
    paths = sorted((pbt.PACKAGE / "prompts").rglob("*.py"))
    modules, tests = pbt.analyze(None, {}, paths=paths)
    assert tests == 0
    assert [m["module"] for m in modules] == [pbt.module_name(p) for p in paths]
    assert all(m["hit"] == 0 for m in modules)
    assert sum(m["statements"] for m in modules) > 0


def test_only_test_files_that_import_hypothesis_are_suite_files(pbt, tmp_path):
    (tmp_path / "pbt").mkdir()
    (tmp_path / "pbt" / "test_props.py").write_text("from hypothesis import given\n")
    (tmp_path / "pbt" / "strategies.py").write_text("import hypothesis\n")
    (tmp_path / "pbt" / "conftest.py").write_text("from hypothesis import settings\n")
    (tmp_path / "test_plain.py").write_text("import pytest\n")
    assert pbt.discover_pbt_tests(tmp_path) == [tmp_path / "pbt" / "test_props.py"]
