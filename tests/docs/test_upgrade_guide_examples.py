"""Execute the Python examples in the upgrade guides.

`test_doc_examples.py` covers every page in `docs/`, but only checks that
examples parse and that their ``fastmcp.*`` imports resolve. The upgrade guides
carry a stronger obligation: someone lands on one mid-migration, copies a block,
and runs it. So these examples are actually executed, and their non-FastMCP
imports (`mcp`, `mcp_types`) are exercised along with everything else.

Both halves of a `<CodeGroup>` are executed where they can be. The "after" code
is FastMCP 4, which this repo is. The "before" code is only runnable when it
targets the MCP SDK **v2** — the version installed here — which covers the two
SDK v2 guides. Blocks written against SDK v1 (whose `mcp.types` and
`mcp.server.fastmcp` no longer exist) and fragments that pair a "# Before" and
"# After" in one block are tagged ``test="skip"`` in the source and skipped here;
the count of those is pinned so a new one can't appear unnoticed.

Run:
    uv run pytest tests/docs/test_upgrade_guide_examples.py -v
"""

from __future__ import annotations

import warnings
from pathlib import Path
from uuid import uuid4

import pytest
from pytest_examples import CodeExample
from pytest_examples.find_examples import _extract_code_chunks

import fastmcp

UPGRADE_DIR = Path("docs/getting-started/upgrading")

# Blocks deliberately not executable: SDK v1 API that is no longer installable,
# and before/after fragments that are not standalone programs. Pinned so that
# adding a skip is a visible decision rather than a silent one.
EXPECTED_SKIPS = 36


def _examples() -> list[CodeExample]:
    examples: list[CodeExample] = []
    for mdx_file in sorted(UPGRADE_DIR.rglob("*.mdx")):
        code = mdx_file.read_text("utf-8")
        examples.extend(_extract_code_chunks(mdx_file, code, uuid4()))
    return examples


ALL = _examples()
RUNNABLE = [ex for ex in ALL if ex.prefix_settings().get("test") != "skip"]
SKIPPED = [ex for ex in ALL if ex.prefix_settings().get("test") == "skip"]


def _example_id(example: CodeExample) -> str:
    return f"{Path(example.path).name}:{example.start_line}"


def test_guides_have_examples():
    """Guard against the extractor silently matching nothing."""
    assert len(RUNNABLE) >= 20, f"only found {len(RUNNABLE)} runnable examples"


def test_skip_count_is_pinned():
    """A newly unrunnable example should be a deliberate choice."""
    listing = "\n".join(f"  {_example_id(ex)}" for ex in SKIPPED)
    assert len(SKIPPED) == EXPECTED_SKIPS, (
        f"expected {EXPECTED_SKIPS} skipped examples, found {len(SKIPPED)}:\n{listing}"
    )


@pytest.fixture(autouse=True)
def restore_global_settings():
    """Undo any global setting an example changes.

    Some examples exist precisely to show a global toggle — the upgrade guide
    demonstrates turning the camelCase bridge off with
    ``fastmcp.settings.mcp_camelcase_compat = False``. Executing that here
    would otherwise leave the bridge off for every test that runs afterwards in
    the same process, which silently breaks unrelated suites.
    """
    before = fastmcp.settings.model_dump()
    yield
    for field, value in before.items():
        if getattr(fastmcp.settings, field, value) != value:
            setattr(fastmcp.settings, field, value)


@pytest.mark.parametrize("example", RUNNABLE, ids=[_example_id(e) for e in RUNNABLE])
def test_example_executes(example: CodeExample):
    """Every non-skipped example runs top to bottom without raising.

    Examples are executed under a module name other than ``__main__`` so an
    ``if __name__ == "__main__": mcp.run()`` footer defines the server without
    starting it.
    """
    namespace: dict[str, object] = {"__name__": "fastmcp_docs_example"}
    with warnings.catch_warnings():
        # Guides intentionally demonstrate deprecated surfaces (the camelCase
        # bridge, SDK logging) whose warnings are the point being made.
        warnings.simplefilter("ignore")
        exec(compile(example.source, str(example.path), "exec"), namespace)
