from pathlib import Path

from scripts.draft_release import entries, new_entries

ENTRY = """<Update label="v9.9.9" description="2026-11-20">

**[v9.9.9: Test Pattern](https://github.com/PrefectHQ/fastmcp/releases/tag/v9.9.9)**

FastMCP 9.9.9 fixes a thing. Thanks to our 2 contributors, including 1 first-time contributor.

### Fixes 🐞
* Fix a thing by [@alice](https://github.com/alice) and [@bob](https://github.com/bob) in [#1](https://github.com/PrefectHQ/fastmcp/pull/1)

## New Contributors
* @bob made their first contribution in [#1](https://github.com/PrefectHQ/fastmcp/pull/1)

**Full Changelog**: [v9.9.8...v9.9.9](https://github.com/PrefectHQ/fastmcp/compare/v9.9.8...v9.9.9)

</Update>
"""


def test_entry_becomes_github_release_notes():
    entry = entries(ENTRY)["v9.9.9"]

    assert entry.title == "v9.9.9: Test Pattern"
    assert not entry.prerelease
    assert entry.body == (
        "FastMCP 9.9.9 fixes a thing. Thanks to our 2 contributors, including 1 "
        "first-time contributor.\n\n"
        "## What's Changed\n"
        "### Fixes 🐞\n"
        "* Fix a thing by @alice and @bob in https://github.com/PrefectHQ/fastmcp/pull/1\n\n"
        "## New Contributors\n"
        "* @bob made their first contribution in https://github.com/PrefectHQ/fastmcp/pull/1\n\n"
        "**Full Changelog**: https://github.com/PrefectHQ/fastmcp/compare/v9.9.8...v9.9.9\n"
    )


def test_only_added_entries_are_drafted():
    existing = Path("docs/changelog.mdx").read_text(encoding="utf-8")
    head, sep, rest = existing.partition("<Update ")
    after = head + ENTRY + "\n" + sep + rest

    assert [entry.tag for entry in new_entries(existing, after)] == ["v9.9.9"]
    assert new_entries(after, after) == []


def test_prerelease_tags_draft_as_prereleases():
    text = ENTRY.replace("v9.9.9", "v9.10.0a1")

    assert entries(text)["v9.10.0a1"].prerelease


def test_current_entries_parse():
    parsed = entries(
        Path("docs/changelog.mdx")
        .read_text(encoding="utf-8")
        .split('<Update label="v4.2.2"')[0]
    )

    assert parsed["v4.2.3"].title == "v4.2.3: Trace Elements"
