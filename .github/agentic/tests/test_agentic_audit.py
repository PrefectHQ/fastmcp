from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

import flows.agentic_audit as audit
from flows.agentic_stack_plans import OUTPUTS, STANDALONE_ROLES, Audit

NOW = datetime(2026, 11, 24, 13, tzinfo=UTC)
BOT = {"login": "fastmcp-triage-reviewer[bot]", "type": "Bot"}
NATE = {"login": "zzstoatzz", "type": "User"}


def at(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def event(kind: str, number: int, hours: float, actor=BOT, label="", pr=False) -> dict:
    issue = {"number": number}
    if pr:
        issue["pull_request"] = {}
    return {
        "event": kind,
        "issue": issue,
        "actor": actor,
        "label": {"name": label} if label else None,
        "created_at": at(hours),
    }


def test_audit_plan_reads_the_config_key():
    flow_id, version_id = uuid4(), uuid4()
    plan = audit.audit_plan(
        {"audit": {"flow_id": str(flow_id), "version_id": str(version_id)}}
    )
    assert plan is not None and plan.flow_id == flow_id
    assert audit.audit_plan({}) is None
    assert audit.audit_plan(None) is None


def test_recent_events_stop_at_the_window():
    pages = {1: [event("labeled", 1, 1, label="needs MRE"), event("closed", 2, 30)]}
    found = audit.recent_events(
        lambda path: pages[int(path.rsplit("=", 1)[1])], NOW - timedelta(hours=26)
    )
    assert [e["issue"]["number"] for e in found] == [1]


def test_automated_writes_are_clock_labels_and_closures_on_issues():
    events = [
        event("labeled", 1, 1, label="needs MRE"),
        event("labeled", 2, 1, label="needs:triage"),
        event("labeled", 3, 1, label="needs MRE", actor=NATE),
        event("closed", 4, 2, actor={"login": "github-actions[bot]", "type": "Bot"}),
        event("closed", 5, 2, pr=True),
    ]
    writes = audit.automated_writes(events)
    assert [(w["number"], w["write"]) for w in writes] == [
        (1, "added needs MRE"),
        (4, "closed"),
    ]


def test_reversals_are_people_undoing_automation():
    # #6630: the triage app added needs MRE, and a maintainer took it off.
    events = [
        event("unlabeled", 6630, 1, actor=NATE, label="needs MRE"),
        event("unlabeled", 7, 1, actor=NATE, label="needs MRE"),
        event("reopened", 8, 1, actor=NATE),
        event("unlabeled", 9, 1, actor=NATE, label="server"),
    ]
    history = {
        6630: [event("labeled", 6630, 50, label="needs MRE")],
        7: [event("labeled", 7, 50, actor=NATE, label="needs MRE")],
        8: [
            event(
                "closed", 8, 50, actor={"login": "github-actions[bot]", "type": "Bot"}
            )
        ],
        9: [],
    }
    found = audit.reversals(events, history.__getitem__)
    assert [(r["number"], r["write"], r["undone_by"]) for r in found] == [
        (6630, "added needs MRE", "zzstoatzz"),
        (8, "closed", "zzstoatzz"),
    ]


def test_clocks_list_open_issues_by_label():
    def pages(path):
        if "needs%20MRE" in path:
            return [{"number": 3}, {"number": 1, "pull_request": {}}]
        if "sweep%3Aclose" in path:
            return [{"number": 3}, {"number": 2}]
        return []

    assert audit.clocks(pages) == [
        {"number": 2, "labels": ["sweep:close"]},
        {"number": 3, "labels": ["needs MRE", "sweep:close"]},
    ]


def test_subject_keeps_the_latest_human_comments():
    def request(path):
        return {
            "title": "t",
            "state": "open",
            "user": NATE,
            "labels": [{"name": "bug"}],
            "body": "b",
        }

    def pages(path):
        assert path.endswith("/comments")
        older = [{"user": NATE, "body": f"old {n}"} for n in range(120)]
        return older + [
            {"user": BOT, "body": "Thanks for the report."},
            {"user": NATE, "author_association": "MEMBER", "body": "real bug"},
        ]

    item = audit.subject(request, pages, 5)
    assert len(item["comments"]) == 5
    assert item["comments"][-1] == {
        "author": "zzstoatzz",
        "association": "MEMBER",
        "body": "real bug",
    }
    assert item["labels"] == ["bug"]


def test_audit_is_a_standalone_role_that_only_reports():
    assert OUTPUTS["audit"] is Audit
    assert "audit" in STANDALONE_ROLES
    Audit.model_validate(
        {"mistakes": [], "post": False, "text": "", "why": "checked 4"}
    )
    with pytest.raises(ValidationError):
        Audit.model_validate({"mistakes": [], "post": False, "text": "", "why": ""})
