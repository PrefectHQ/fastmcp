"""Example clients must not ping without pinning the legacy protocol era.

The default client negotiates the modern era, where `ping` is not a method,
so an unpinned `client.ping()` fails with "Method not found".
"""

import ast
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _calls_ping(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ping"
        for node in ast.walk(tree)
    )


def _pins_legacy_mode(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.keyword)
        and node.arg == "mode"
        and isinstance(node.value, ast.Constant)
        and node.value.value == "legacy"
        for node in ast.walk(tree)
    )


def test_example_clients_only_ping_in_legacy_mode():
    offenders = []
    for path in sorted(EXAMPLES_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        if _calls_ping(tree) and not _pins_legacy_mode(tree):
            offenders.append(str(path.relative_to(EXAMPLES_DIR)))
    assert offenders == []
