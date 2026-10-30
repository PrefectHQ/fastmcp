import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def garden():
    path = Path(__file__).parents[1] / "scripts" / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pypi(stable: str, uploads: dict[str, str], yanked: tuple[str, ...] = ()):
    return {
        "info": {"version": stable},
        "releases": {
            version: [{"upload_time_iso_8601": when, "yanked": version in yanked}]
            for version, when in uploads.items()
        },
    }


def test_release_targets_include_prerelease_newer_than_stable(garden):
    data = pypi(
        "4.1.4",
        {"4.1.4": "2026-10-01", "4.2.0a1": "2026-10-20", "3.4.11": "2026-10-25"},
    )
    # A 3.x patch uploaded after the alpha must not hide it.
    assert garden.release_targets(data) == ("4.1.4", "4.2.0a1")


def test_release_targets_skip_prerelease_superseded_by_stable(garden):
    data = pypi("4.2.0", {"4.2.0a1": "2026-10-20", "4.2.0": "2026-10-28"})
    assert garden.release_targets(data) == ("4.2.0", None)


def test_release_targets_ignore_yanked(garden):
    data = pypi(
        "4.1.4",
        {"4.1.4": "2026-10-01", "4.2.0a2": "2026-10-29"},
        yanked=("4.2.0a2",),
    )
    assert garden.release_targets(data) == ("4.1.4", None)


def test_section_keeps_errors(garden):
    def broken():
        raise RuntimeError("gh is not authenticated")

    body = garden.section("ci", broken)
    assert body["error"] == "RuntimeError: gh is not authenticated"
    assert "seconds" in body


def test_markdown_renders_skipped_and_failed_sections(garden):
    report = {
        "generated_at": "2026-10-30T21:00:00+00:00",
        "published": {"skipped": True},
        "unreleased": {"since": [{"base": "v4.2.0a1", "error": "boom"}]},
        "ci": {"skipped": True},
    }
    text = garden.markdown(report)
    assert "Skipped." in text
    assert "## Unreleased since v4.2.0a1" in text
    assert "Could not run: boom" in text


def test_loq_caps_ignore_globs(garden):
    text = """
default_max_lines = 1000

[[rules]]
path = "tests/**"
max_lines = 1000

[[rules]]
path = "fastmcp_slim/fastmcp/server/server.py"
max_lines = 2410
"""
    assert garden.loq_caps(text) == {"fastmcp_slim/fastmcp/server/server.py": 2410}
