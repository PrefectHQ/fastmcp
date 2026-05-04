"""Tests for fastmcp.utilities.versions.

Regression tests for the dedupe_with_versions deep-copy fix.
Refs https://github.com/PrefectHQ/fastmcp/issues/4055 (bug 2).
"""

from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import dedupe_with_versions


def _make_tool(name: str, version: str | None, meta: dict | None = None) -> Tool:
    return Tool(
        name=name,
        description=f"Tool {name} v{version}",
        parameters={"type": "object", "properties": {}},
        version=version,
        meta=meta,
    )


class TestDedupeWithVersionsMetaIsolation:
    """The deduped component must not share nested mutable state with the
    original component (or with sibling versions).

    Without `model_copy(deep=True)` the inner dicts/lists in `meta` are
    aliased between the deduped result and the input components — a
    mutation on one leaks into the others.
    """

    def test_mutating_dedupe_meta_does_not_leak_to_original(self):
        # Two versions of the same tool, both with nested mutable meta.
        original_meta = {"shared": {"counter": 0}, "tags_list": ["a"]}
        v1 = _make_tool("hello", "1.0", meta={**original_meta})
        v2 = _make_tool("hello", "2.0", meta={**original_meta})

        result = dedupe_with_versions([v1, v2], lambda t: t.name)
        assert len(result) == 1
        deduped = result[0]
        assert deduped.version == "2.0"

        # Mutate a nested dict and a nested list on the deduped copy.
        assert deduped.meta is not None
        deduped.meta["shared"]["counter"] = 999
        deduped.meta["tags_list"].append("MUTATED")

        # The original components must be untouched.
        assert v1.meta is not None and v2.meta is not None
        assert v1.meta["shared"]["counter"] == 0
        assert v2.meta["shared"]["counter"] == 0
        assert v1.meta["tags_list"] == ["a"]
        assert v2.meta["tags_list"] == ["a"]

    def test_dedupe_injects_versions_metadata(self):
        # Sanity check: the existing behavior of recording all versions
        # under meta["fastmcp"]["versions"] still works after deep-copying.
        v1 = _make_tool("hello", "1.0")
        v2 = _make_tool("hello", "2.0")

        result = dedupe_with_versions([v1, v2], lambda t: t.name)
        assert len(result) == 1
        deduped = result[0]
        assert deduped.meta is not None
        assert deduped.meta["fastmcp"]["versions"] == ["2.0", "1.0"]
