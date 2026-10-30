from copy import deepcopy

import pytest

from flows.agentic_intake import (
    directly_addresses,
    evidence_revision,
    held,
    route_intake,
)
from flows.agentic_stack_policy import publication_revision


@pytest.fixture
def evidence():
    return {
        "schema_version": 2,
        "complete": True,
        "collected_at": "2026-10-21T00:00:00Z",
        "base_sha": "a" * 40,
        "issue": {
            "number": 2692,
            "state": "open",
            "labels": [],
            "assignees": [],
            "user": {"login": "reporter"},
            "updated_at": "2026-10-20T00:00:00Z",
        },
        "comments": [],
        "timeline": [],
        "prs": [],
        "reactions": [],
    }


def comment(number, date, body, login="reporter", kind="User"):
    return {
        "id": number,
        "created_at": date,
        "body": body,
        "user": {"login": login, "type": kind},
    }


@pytest.mark.parametrize("revision", [evidence_revision, publication_revision])
def test_unrelated_repository_activity_does_not_invalidate_contribution(
    evidence, revision
):
    original = {
        **evidence,
        "stacks": [],
        "timeline": [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "number": 7,
                        "state": "open",
                        "repository": {
                            "id": 1,
                            "full_name": "PrefectHQ/fastmcp",
                            "stargazers_count": 28161,
                            "pushed_at": "2026-10-21T15:23:00Z",
                        },
                    }
                },
            }
        ],
    }
    original["prs"] = [{"number": 9, "timeline": deepcopy(original["timeline"])}]
    refreshed = deepcopy(original)
    for timeline in (refreshed["timeline"], refreshed["prs"][0]["timeline"]):
        repository = timeline[0]["source"]["issue"]["repository"]
        repository["stargazers_count"] += 1
        repository["pushed_at"] = "2026-10-21T16:28:29Z"
    assert revision(original) == revision(refreshed)
    assert (
        original["timeline"][0]["source"]["issue"]["repository"]["stargazers_count"]
        == 28161
    )
    changed = deepcopy(refreshed)
    changed["timeline"][0]["source"]["issue"]["state"] = "closed"
    assert revision(original) != revision(changed)
    changed = deepcopy(refreshed)
    changed["prs"][0]["timeline"][0]["source"]["issue"]["repository"]["id"] = 2
    assert revision(original) != revision(changed)


def test_intake_never_rejects_a_report_for_missing_labels(evidence):
    assert route_intake(evidence)["route"] == "triage"
    evidence["issue"]["labels"] = ["enhancement", "low-priority", "auth"]
    result = route_intake(evidence)
    assert result["route"] == "triage"
    assert result["areas"] == ["auth"]
    assert result["label_writes"] == []


def test_closed_gate_contribution_is_reviewed_without_new_fix(evidence):
    evidence["prs"] = [
        {
            "number": 1,
            "body": "Fixes #2692",
            "state": "closed",
            "merged": False,
            "labels": ["missing-issue-link"],
            "draft": False,
        }
    ]
    result = route_intake(evidence)
    assert result["route"] == "review_existing"
    assert result["pull_requests"] == [
        {"number": 1, "state": "closed", "held": False, "merged": False}
    ]


@pytest.mark.parametrize(
    "body",
    [
        "Fixes #26920",
        "Fixes other/repo#2692",
        "References #2692",
        "Fixes https://github.com/other/repo/issues/2692",
    ],
)
def test_related_or_foreign_references_need_semantic_matching(body):
    assert not directly_addresses(body, 2692)


@pytest.mark.parametrize(
    "body",
    [
        "Closes: #2692",
        "Fixes PrefectHQ/fastmcp#2692",
        "Resolves https://github.com/PrefectHQ/fastmcp/issues/2692",
    ],
)
def test_explicit_fixing_references_route_without_model(body):
    assert directly_addresses(body, 2692)


@pytest.mark.parametrize(
    "pr",
    [
        {"draft": True},
        {"title": "[DNM] fix"},
        {"body": "DO NOT MERGE"},
        {"labels": ["DON'T MERGE"]},
        {"labels": ["dont-merge"]},
    ],
)
def test_hold_survives_title_body_labels_and_draft_metadata(pr):
    assert held(pr)


@pytest.mark.parametrize(
    "activity", ["comment", "reaction", "reopened", "previous_closure"]
)
def test_duplicate_human_intervention_cannot_repeat_pruning(evidence, activity):
    evidence["issue"]["labels"] = ["potential-duplicate", "invalid"]
    evidence["comments"] = [
        comment(1, "2026-10-15T00:00:00Z", "Possible duplicate issues", "marvin", "Bot")
    ]
    if activity == "comment":
        evidence["comments"].append(
            comment(2, "2026-10-16T00:00:00Z", "Different bug", "another-human")
        )
    elif activity == "reaction":
        evidence["reactions"] = [{"comment_id": 1, "content": "confused"}]
    elif activity == "reopened":
        evidence["timeline"] = [
            {"event": "reopened", "created_at": "2026-10-17T00:00:00Z"}
        ]
    else:
        evidence["comments"].append(
            comment(
                2,
                "2026-10-18T00:00:00Z",
                "Closing this issue as a duplicate",
                "marvin",
                "Bot",
            )
        )
    result = route_intake(evidence)
    assert result["route"] == "triage"
    assert result["human_intervention"]


def test_mre_response_invalidates_waiting_route(evidence):
    evidence["issue"]["labels"] = ["needs MRE"]
    evidence["timeline"] = [
        {
            "event": "labeled",
            "created_at": "2026-10-20T00:00:00Z",
            "label": {"name": "needs MRE"},
        }
    ]
    assert route_intake(evidence)["route"] == "evidence_needed"
    evidence["comments"].append(comment(2, "2026-10-21T00:00:00Z", "Here is a repro"))
    assert route_intake(evidence)["route"] == "triage"


def test_failed_retrieval_is_an_operational_retry(evidence):
    evidence["complete"] = False
    assert route_intake(evidence)["route"] == "retry"


def test_evidence_cache_survives_scan_but_invalidates_new_information(evidence):
    original = evidence_revision(evidence)
    evidence["collected_at"] = "2026-10-22T00:00:00Z"
    assert evidence_revision(evidence) == original
    for field, value in [
        ("base_sha", "b" * 40),
        ("comments", [comment(1, "2026-10-21T00:00:00Z", "New evidence")]),
    ]:
        changed = deepcopy(evidence)
        changed[field] = value
        assert evidence_revision(changed) != original
    evidence["issue"]["labels"].append("needs MRE")
    assert evidence_revision(evidence) != original


def test_security_routes_before_public_contribution_review(evidence):
    evidence["issue"]["labels"] = ["security"]
    evidence["prs"] = [{"body": "Fixes #2692"}]
    assert route_intake(evidence)["route"] == "restricted_review"
