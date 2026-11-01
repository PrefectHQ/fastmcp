"""Counts, outcomes, and imported evidence for the public backlog charts."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def backlog():
    spec = importlib.util.spec_from_file_location(
        "backlog_status", Path(__file__).parents[1] / "scripts" / "backlog_status.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def item(
    number,
    *,
    pr=False,
    created="2026-10-19T15:00:00Z",
    closed=None,
    reason=None,
    merged=None,
    draft=False,
):
    return {
        "number": number,
        "title": f"Item {number}",
        "created_at": created,
        "state": "closed" if closed else "open",
        "closed_at": closed,
        "state_reason": reason,
        "draft": draft,
        "pull_request": {"merged_at": merged} if pr else None,
    }


def test_closure_outcomes_and_local_midnight(backlog):
    now = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)
    items = [
        item(1, closed="2026-10-20T04:59:59Z", reason="completed"),
        item(2, closed="2026-10-20T05:00:00Z", reason="not_planned"),
        item(3, closed="2026-10-20T06:00:00Z"),
        item(4, pr=True, closed="2026-10-20T07:00:00Z", merged="2026-10-20T07:00:00Z"),
        item(5, pr=True, closed="2026-10-20T08:00:00Z"),
        item(6, pr=True, draft=True),
    ]
    doc = backlog.build(items, {}, now)
    yesterday, today = doc["daily"][-2:]
    assert yesterday["completed"] == 1
    assert today["completed"] == 0
    assert today["not_planned"] == today["other_closed"] == 1
    assert today["merged"] == today["closed_unmerged"] == 1
    assert doc["samples"][-1]["issues"]["open"] == 0
    assert doc["samples"][-1]["prs"] == {"open": 1, "draft": 1}
    assert len([event for event in doc["events"] if event["number"] == 4]) == 2


def test_preserves_observed_counts_when_reopened_history_is_incomplete(backlog):
    previous_sample = {
        "at": "2026-10-19T18:00:00Z",
        "source": "live",
        "issues": {"open": 0},
        "prs": {"open": 0, "draft": 0},
    }
    reopened = item(1)
    doc = backlog.build(
        [reopened],
        {"samples": [previous_sample]},
        datetime(2026, 10, 20, 14, tzinfo=timezone.utc),
    )
    assert doc["samples"][0] == previous_sample
    assert doc["samples"][-1]["issues"]["open"] == 1
    assert all(row["completed"] == 0 for row in doc["daily"])
    assert [event["event"] for event in doc["events"]] == ["issues_opened"]


def test_reconstructed_counts_do_not_invent_historical_drafts(backlog):
    items = [
        item(1, pr=True, created="2026-10-18T00:00:00Z", draft=True),
        item(2, created="2026-10-20T00:00:00Z"),
    ]
    sample = backlog.sample(
        items, datetime(2026, 10, 19, tzinfo=timezone.utc), "backfill"
    )
    assert sample["issues"] == {"open": 0}
    assert sample["prs"] == {"open": 1}


def test_explicit_missing_history_fails_without_reset(backlog, tmp_path, monkeypatch):
    output = tmp_path / "backlog.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "backlog_status.py",
            "--previous",
            str(tmp_path / "missing.json"),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(FileNotFoundError):
        backlog.main()
    assert not output.exists()


def test_truncated_collection_fails_without_publishing(backlog, tmp_path, monkeypatch):
    previous = tmp_path / "previous.json"
    previous.write_text('{"schema": "fastmcp-backlog/1", "samples": []}')
    output = tmp_path / "backlog.json"
    monkeypatch.setattr(backlog, "newest_number", lambda: 3)
    monkeypatch.setattr(
        backlog,
        "fetch_items",
        lambda: [item(1, closed="2026-10-19T16:00:00Z", reason="completed"), item(2)],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "backlog_status.py",
            "--previous",
            str(previous),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(RuntimeError, match="#3"):
        backlog.main()
    assert not output.exists()


def test_items_created_during_collection_are_accepted(backlog):
    backlog.require_complete([item(1), item(2), item(3)], 2)
