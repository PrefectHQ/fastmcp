"""Contribution intake classifies PRs from GitHub facts without closing good fixes."""

from dataclasses import replace

import pytest

from scripts.contribution_intake import (
    Action,
    IntakeClass,
    LinkedIssue,
    OtherPR,
    PRFacts,
    classify,
    linked_issue_numbers,
    open_under_intake,
    parse_time,
)


def issue(number: int = 100, **overrides) -> LinkedIssue:
    return replace(LinkedIssue(number=number, author="reporter"), **overrides)


def facts(**overrides) -> PRFacts:
    base = PRFacts(number=200, author="contributor", linked_issues=(issue(),))
    return replace(base, **overrides)


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"author_is_bot": True}, "author is a bot"),
        ({"author_has_write": True}, "author has write access"),
        ({"labels": ("trusted-contributor",)}, "PR is labelled trusted-contributor"),
        ({"labels": ("bypass-issue-check",)}, "PR is labelled bypass-issue-check"),
        # Exempt wins even with no link at all.
        ({"author_has_write": True, "linked_issues": ()}, "author has write access"),
    ],
)
def test_exempt(overrides, reason):
    verdict = classify(facts(**overrides))
    assert verdict.intake_class is IntakeClass.EXEMPT
    assert verdict.action is Action.NONE
    assert verdict.reason == reason


def test_no_link_closes():
    # Spam and low-value PRs with no issue reference (e.g. #6351, #6345).
    verdict = classify(facts(linked_issues=()))
    assert verdict.intake_class is IntakeClass.NO_LINK
    assert verdict.action is Action.CLOSE


def test_link_to_missing_issue_is_no_link():
    verdict = classify(facts(linked_issues=(issue(99999, exists=False),)))
    assert verdict.intake_class is IntakeClass.NO_LINK
    assert verdict.reason == "linked issue does not exist"


def test_reporter_stays_open_without_assignment():
    # #6358: the reporter opened a fix for their own issue and was closed.
    verdict = classify(
        facts(
            author="MayankTamakuwala", linked_issues=(issue(author="mayanktamakuwala"),)
        )
    )
    assert verdict.intake_class is IntakeClass.REPORTER
    assert verdict.action is Action.KEEP_OPEN
    assert verdict.issue == 100


def test_reporter_outranks_resolved_issue():
    resolved = issue(author="contributor", state="closed", state_reason="completed")
    verdict = classify(facts(linked_issues=(resolved,)))
    assert verdict.intake_class is IntakeClass.REPORTER


@pytest.mark.parametrize(
    "linked",
    [issue(assignees=("Contributor",)), issue(labels=("PRs Welcome",))],
)
def test_accepted(linked):
    verdict = classify(facts(linked_issues=(linked,)))
    assert verdict.intake_class is IntakeClass.ACCEPTED
    assert verdict.action is Action.KEEP_OPEN


def test_best_fix_from_unassigned_contributor_is_candidate():
    # #6277: the fix that later merged, closed because nobody was assigned yet.
    verdict = classify(
        facts(number=6277, linked_issues=(issue(6273),), author_assignable=False)
    )
    assert verdict.intake_class is IntakeClass.CANDIDATE
    assert verdict.action is Action.LABEL_FOR_VERIFICATION
    assert verdict.issue == 6273
    assert verdict.author_assignable is False


def test_resolved_issue_closes_with_pointer():
    closed = issue(state="closed", state_reason="completed")
    verdict = classify(facts(linked_issues=(closed,)))
    assert verdict.intake_class is IntakeClass.ISSUE_RESOLVED
    assert verdict.action is Action.CLOSE
    assert verdict.pointer == 100


@pytest.mark.parametrize("reason", ["not_planned", "duplicate", None])
def test_issue_closed_without_completion_stays_candidate(reason):
    closed = issue(state="closed", state_reason=reason)
    verdict = classify(facts(linked_issues=(closed,)))
    assert verdict.intake_class is IntakeClass.CANDIDATE


def test_one_open_issue_keeps_pr_out_of_resolved():
    linked = (issue(1, state="closed", state_reason="completed"), issue(2))
    verdict = classify(facts(linked_issues=linked))
    assert verdict.intake_class is IntakeClass.CANDIDATE
    assert verdict.issue == 2


def test_own_duplicate_points_at_oldest_open_pr():
    # The same author re-opening a fix for one issue (e.g. #6326, #6327, #6329).
    others = (OtherPR(6327, (100,)), OtherPR(6326, (100,)), OtherPR(6400, (100,)))
    verdict = classify(facts(number=6329, author_open_prs=others))
    assert verdict.intake_class is IntakeClass.OWN_DUPLICATE
    assert verdict.action is Action.CLOSE
    assert verdict.pointer == 6326


def test_oldest_of_own_duplicates_is_candidate():
    verdict = classify(facts(number=6326, author_open_prs=(OtherPR(6327, (100,)),)))
    assert verdict.intake_class is IntakeClass.CANDIDATE


def test_own_pr_for_another_issue_is_not_duplicate():
    verdict = classify(facts(author_open_prs=(OtherPR(150, (101,)),)))
    assert verdict.intake_class is IntakeClass.CANDIDATE


def test_assignment_outranks_own_duplicate():
    linked = (issue(assignees=("contributor",)),)
    verdict = classify(
        facts(linked_issues=linked, author_open_prs=(OtherPR(150, (100,)),))
    )
    assert verdict.intake_class is IntakeClass.ACCEPTED


def test_verdict_json_uses_plain_values():
    data = classify(facts(linked_issues=())).to_json()
    assert data["intake_class"] == "no_link"
    assert data["action"] == "close"
    assert data["notes"] == []


@pytest.mark.parametrize(
    "body, expected",
    [
        ("Fixes #12", [12]),
        ("closes: #12 and resolves #13, fixed #12", [12, 13]),
        ("Resolves PrefectHQ/fastmcp#44", [44]),
        ("Closes https://github.com/PrefectHQ/fastmcp/issues/45", [45]),
        ("Fixes other/repo#46", []),
        ("Closes https://github.com/other/repo/issues/47", []),
        ("Related to #48", []),
        ("", []),
        (None, []),
        ("Fixes #1 #2", [1]),
        ("fixes #1, fixes #2, fixes #3, fixes #4, fixes #5, fixes #6", [1, 2, 3, 4, 5]),
    ],
)
def test_linked_issue_numbers(body, expected):
    assert linked_issue_numbers(body) == expected


def _event(kind: str, at: str, actor: str = "maintainer") -> dict:
    return {"event": kind, "created_at": at, "actor": {"login": actor}}


GATE_CLOSED = _event("closed", "2026-11-01T00:00:00Z", "github-actions[bot]")
AS_OF = parse_time("2026-11-05T00:00:00Z")


@pytest.mark.parametrize(
    "events, kept_open, expected",
    [
        ([GATE_CLOSED], True, True),
        ([GATE_CLOSED], False, False),
        (
            [
                GATE_CLOSED,
                _event("reopened", "2026-11-02T00:00:00Z"),
                _event("closed", "2026-11-03T00:00:00Z"),
            ],
            True,
            False,
        ),
        ([_event("closed", "2026-11-02T00:00:00Z")], True, False),
        ([GATE_CLOSED, _event("closed", "2026-11-06T00:00:00Z")], True, True),
    ],
)
def test_open_under_intake_discounts_only_the_gate_close(events, kept_open, expected):
    assert open_under_intake(events, AS_OF, kept_open) is expected
