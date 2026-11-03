"""Public analytics preserve uncertainty and do not manufacture review verdicts."""

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)


@pytest.fixture
def triage():
    spec = importlib.util.spec_from_file_location(
        "public_triage", Path(__file__).parents[1] / "scripts/public_triage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def item(number=1, **overrides):
    return {
        "number": number,
        "title": "Fix supported behavior",
        "created_at": "2026-10-01T00:00:00Z",
        "state": "open",
        "pull_request": {},
        "draft": False,
        "author_association": "NONE",
        "user": {"login": "contributor", "type": "User"},
        **overrides,
    }


def pr(**overrides):
    return {
        **item(),
        "head": {"sha": "a" * 40},
        "base": {"sha": "b" * 40, "ref": "main"},
        "updated_at": "2026-11-03T00:00:00Z",
        **overrides,
    }


def activity(login, when, *, bot=False, association="MEMBER", **fields):
    return {
        "user": {"login": login, "type": "Bot" if bot else "User"},
        "author_association": association,
        "created_at": when,
        **fields,
    }


def test_discussion_after_first_page_and_historical_review(triage, monkeypatch):
    # Paginated REST pages include a maintainer after 100 earlier comments.
    comments = [activity("contributor", "2026-10-02T00:00:00Z")] * 100
    last = activity("maintainer", "2026-11-02T00:00:00Z")
    review = activity(
        "fastmcp-triage-reviewer[bot]",
        "2026-10-03T00:00:00Z",
        bot=True,
        commit_id="c" * 40,
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        value = [[*comments], [last]] if "comments" in command[2] else [[review]]
        if "--paginate" not in command:
            value = pr()
        return SimpleNamespace(stdout=json.dumps(value))

    monkeypatch.setattr(triage.subprocess, "run", run)
    report = triage.build([item()], NOW)
    lead = report["review_leads"][0]
    assert lead["activity"]["comments"] == 101
    assert lead["activity"]["last_maintainer_account_activity_at"] == last["created_at"]
    assert lead["activity"]["triage_app_review_seen"] is True
    assert lead["activity"]["triage_app_review_on_snapshot_head"] is False
    assert lead["head_sha"] == "a" * 40
    assert all("--slurp" in command for command in calls[1:])


def test_failed_activity_is_unknown_not_unanswered(triage, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 20)

    monkeypatch.setattr(triage, "github", failed)
    report = triage.build([item()], NOW)
    lead = report["review_leads"][0]
    assert lead["activity"] == {"status": "unavailable"}
    assert "first_maintainer_account_response_at" not in lead["activity"]
    assert "collection_gap" in lead
    assert report["open_age_buckets"]["prs"]["30_90d"] == 1


def test_bot_and_author_are_not_maintainer_response(triage, monkeypatch):
    comments = [
        activity("contributor", "2026-10-02T00:00:00Z"),
        activity("bot", "2026-10-03T00:00:00Z", bot=True),
    ]
    monkeypatch.setattr(
        triage,
        "github",
        lambda path, **kw: pr() if not kw else comments if "comments" in path else [],
    )
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert lead["activity"]["status"] == "collected"
    assert lead["activity"]["first_maintainer_account_response_at"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"draft": True},
        {"title": "[DNM] Fix"},
        {"body": "DO NOT MERGE"},
        {"labels": [{"name": "dont-merge"}]},
        {"author_association": "MEMBER"},
        {"user": {"login": "bot", "type": "Bot"}},
    ],
)
def test_holds_drafts_bots_and_maintainers_are_not_leads(triage, monkeypatch, change):
    monkeypatch.setattr(
        triage, "github", lambda *a, **kw: pytest.fail("must not inspect excluded PR")
    )
    report = triage.build([item(**change)], NOW)
    assert not report["review_leads"]
    assert sum(report["open_age_buckets"]["prs"].values()) == 1


def test_hold_after_inventory_prevents_nomination(triage, monkeypatch):
    monkeypatch.setattr(triage, "github", lambda *a, **kw: pr(title="[DRAFT] held"))
    report = triage.build([item()], NOW)
    assert report["selection"]["eligible_in_inventory"] == 1
    assert not report["review_leads"]


def test_age_boundaries_and_empty_success_are_explicit(triage):
    dates = ["2026-11-03", "2026-10-27", "2026-10-04", "2026-08-05"]
    items = [
        item(i, pull_request=None, created_at=d + "T18:00:00Z")
        for i, d in enumerate(dates)
    ]
    report = triage.build(items, NOW)
    assert report["open_age_buckets"]["issues"] == {
        "lt_7d": 1,
        "7_30d": 1,
        "30_90d": 1,
        "gte_90d": 1,
    }
    empty = triage.build([], NOW)
    assert empty["inventory_items"] == 0
    assert empty["collection_started_at"] == NOW.isoformat()
    assert empty["collection_finished_at"]
    assert empty["selection"]["returned"] == 0


def test_bounded_queue_keeps_counts_and_does_not_export_bodies(triage, monkeypatch):
    calls = []

    def inspect(number, now):
        calls.append(number)
        return {"activity": {"status": "unavailable"}}

    monkeypatch.setattr(triage, "inspect_pr", inspect)
    report = triage.build(
        [item(2, body="unpublished-sentinel input"), item(1)], NOW, limit=1
    )
    assert calls == [1]
    assert report["selection"]["eligible_in_inventory"] == 2
    assert sum(report["open_age_buckets"]["prs"].values()) == 2
    assert "unpublished-sentinel" not in json.dumps(report)


def test_counts_use_collection_start_like_backlog_chart(triage):
    report = triage.build(
        [
            item(
                1, pull_request=None, state="closed", closed_at="2026-11-03T18:01:00Z"
            ),
            item(
                2, pull_request=None, state="closed", closed_at="2026-11-03T17:59:00Z"
            ),
            item(3, pull_request=None, created_at="2026-11-03T18:01:00Z"),
        ],
        NOW,
    )
    assert sum(report["open_age_buckets"]["issues"].values()) == 1


def test_discussion_failure_preserves_successful_head_observation(triage, monkeypatch):
    def fetch(path, **kwargs):
        if kwargs:
            raise subprocess.TimeoutExpired("gh", 20)
        return pr()

    monkeypatch.setattr(triage, "github", fetch)
    lead = triage.build([item()], NOW)["review_leads"][0]
    assert lead["head_sha"] == "a" * 40
    assert lead["pr_collected_at"]
    assert lead["activity"] == {"status": "unavailable"}
    assert "collection_gap" in lead
