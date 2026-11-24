import subprocess
from uuid import uuid4

import pytest
from pydantic import ValidationError

import flows.agentic_triage as triage
from flows.agentic_backlog import Intake
from flows.agentic_outcome import next_steps, outcome_lines, subjects
from flows.agentic_triage import announce, maintainer

URL = "https://github.com/PrefectHQ/fastmcp/issues/"

# Trimmed from retained controller review bins.
REVIEW = {
    "bins": {
        "existing_review": [
            {
                "issue_number": 6303,
                "route": {
                    "route": "review_existing",
                    "pull_requests": [
                        {
                            "number": 6309,
                            "held": False,
                            "merged": False,
                            "state": "closed",
                        }
                    ],
                },
                "verification": [
                    {"pr_number": 6309, "status": "verification_recorded"}
                ],
                "status": "verification_recorded",
                "merge_ready": False,
            }
        ],
        "decisions": [
            {
                "issue_number": 6316,
                "decision": "maintainer",
                "reason": "The report describes a credible production limitation.",
                "question": "Should FastMCP add and maintain a supported TasksExtension customization seam for alternate Docket Workers, given the reported ElastiCache Serverless Valkey limitation?",
                "verification_prs": [],
            },
            {
                "issue_number": 6295,
                "reason": "Security-labeled report requires restricted review",
                "route": {"route": "restricted_review"},
            },
        ],
        "pruning": [
            {
                "issue_number": 6344,
                "decision": "prune",
                "reason": "The request is promotional rather than a FastMCP defect. Maintaining vendor-specific instructions would create drift.",
                "question": "Confirm whether to close this promotional catalog request.",
                "verification_prs": [],
            }
        ],
        "evidence": [{"issue_number": 6400, "reason": "Needs a concrete reproduction"}],
        "unchanged": [{"issue_number": 6401, "reason": "Closed"}],
        "operational": [{"issue_number": 6402, "reason": "Verification setup failed"}],
    }
}
ISSUES = [
    {
        "issue_number": 6303,
        "title": "A proxy served over stdio forwards only some of the backend's `ctx.log()` messages",
        "author": "alpaylan",
    },
    {
        "issue_number": 6316,
        "title": "fastmcp-tasks: let TasksExtension run a custom docket Worker",
        "author": "rob-archivium",
    },
    {"issue_number": 6295, "title": "Redirect <URI> & helpers", "author": "alpaylan"},
    {"issue_number": 6344, "title": "Add CrakSEO MCP connect skills", "author": None},
]


def test_outcome_lists_only_items_waiting_on_a_maintainer():
    assert outcome_lines(REVIEW, ISSUES) == [
        f"<{URL}6303|#6303> A proxy served over stdio forwards only some of t… · @alpaylan → review PR #6309",
        f"<{URL}6316|#6316> fastmcp-tasks: let TasksExtension run a custom do… · @rob-archivium → Should FastMCP add and maintain a supported TasksExtension…",
        f"<{URL}6295|#6295> Redirect &lt;URI&gt; &amp; helpers · @alpaylan → Security-labeled report requires restricted review",
        f"<{URL}6344|#6344> Add CrakSEO MCP connect skills → close? The request is promotional rather than a FastMCP defect.",
        f"<{URL}6400|#6400> → needs repro info: Needs a concrete reproduction",
    ]


def test_outcome_prefers_verified_then_open_pull_requests():
    def line(item):
        return outcome_lines({"bins": {"existing_review": [item]}}, [])[0]

    route = {
        "pull_requests": [
            {"number": 1, "state": "closed"},
            {"number": 2, "state": "open"},
        ]
    }
    verified = [{"pr_number": n} for n in (5, 3, 5, 4, 6)]
    assert line({"issue_number": 9, "route": route}).endswith("→ review PR #2")
    assert line({"issue_number": 9, "route": route, "verification": verified}).endswith(
        "→ review PRs #5, #3, #4"
    )
    assert line({"issue_number": 9, "route": {}}).endswith(
        "→ review the linked contribution"
    )


def test_outcome_caps_the_message():
    many = {"bins": {"evidence": [{"issue_number": n} for n in range(1, 10)]}}
    lines = outcome_lines(many, [])
    assert len(lines) == 6
    assert lines[-1] == "…and 4 more in the review bins"
    assert (
        outcome_lines({"bins": {"evidence": many["bins"]["evidence"][:6]}}, [])[-1]
        == f"<{URL}6|#6> → needs repro info"
    )


def test_subjects_come_from_evidence_with_inventory_fallback(evidence):
    originals = {
        1: {**evidence, "issue": {**evidence["issue"], "title": "From evidence"}},
        2: evidence,
    }
    pending = [
        {"issue_number": n, "title": f"Report {n}", "attempts": 0} for n in (1, 2, 3)
    ]
    assert subjects(originals, pending) == [
        {"issue_number": 1, "title": "From evidence", "author": "reporter"},
        {"issue_number": 2, "title": "Report 2", "author": "reporter"},
        {"issue_number": 3, "title": "Report 3", "author": None},
    ]


@pytest.mark.parametrize(
    "review,recorded",
    [
        (REVIEW, True),
        ({"bins": {"unchanged": REVIEW["bins"]["unchanged"]}}, False),
        ({"bins": {"operational": REVIEW["bins"]["operational"]}}, False),
        (None, False),
    ],
)
def test_announce_records_next_steps_without_posting(monkeypatch, review, recorded):
    calls = []
    posted = []
    monkeypatch.setattr(triage, "record_next_step", lambda *args: calls.append(args))
    monkeypatch.setattr(triage, "emit_event", lambda **event: posted.append(event))
    monkeypatch.setattr(triage, "live_item", lambda number: None)
    owner = uuid4()
    announce(owner, {"review": review, "issues": ISSUES})
    assert posted == []
    if not recorded:
        assert calls == []
        return
    assert calls == [
        (owner, number, line)
        for number, line in next_steps(REVIEW, ISSUES, lambda number: None).items()
    ]
    assert len(calls) == len(outcome_lines(REVIEW, ISSUES))


@pytest.mark.parametrize(
    "grant,expected",
    [
        ({"permission": "admin", "role_name": "admin"}, True),
        ({"permission": "write", "role_name": "maintain"}, True),
        ({"permission": "write", "role_name": "write"}, True),
        ({"permission": "read", "role_name": "triage"}, False),
        ({"permission": "read", "role_name": "read"}, False),
    ],
)
def test_maintainer_senders_have_write_access(grant, expected):
    paths = []

    def read(path):
        paths.append(path)
        return grant

    assert maintainer("zzstoatzz", read) is expected
    assert paths == ["repos/PrefectHQ/fastmcp/collaborators/zzstoatzz/permission"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("gh api exited 1: HTTP 403"),
        subprocess.TimeoutExpired("gh", 60),
        ValueError("Expecting value"),
    ],
)
def test_permission_lookup_failure_still_triages(error):
    def read(path):
        raise error

    assert maintainer("contributor", read) is False
    assert maintainer("contributor", lambda path: None) is False


def test_intake_sender_is_a_github_login():
    assert Intake().sender is None
    assert Intake(sender="zzstoatzz").sender == "zzstoatzz"
    for invalid in ("", "-leading", "has space", "bot[bot]"):
        with pytest.raises(ValidationError):
            Intake(sender=invalid)


def test_evidence_says_what_the_reproduction_found():
    def line(item):
        return outcome_lines({"bins": {"evidence": [item]}}, [])[0]

    assert line(
        {
            "issue_number": 1,
            "decision": "reproduced",
            "observed": "Missing path param raises ValueError. More detail.",
            "next_step": "Fix OpenAPITool.run to raise ValidationError for user_id.",
        }
    ).endswith(
        "→ reproduced: Fix OpenAPITool.run to raise ValidationError for user_id."
    )
    assert line(
        {"issue_number": 1, "decision": "not_reproduced", "observed": "Works on main."}
    ).endswith("→ could not reproduce: Works on main.")
    assert line({"issue_number": 1, "decision": "evidence_needed"}).endswith(
        "→ needs repro info"
    )


def test_reproduced_issue_names_who_offered_a_fix():
    line = outcome_lines(
        {
            "bins": {
                "evidence": [
                    {
                        "issue_number": 1,
                        "decision": "reproduced",
                        "observed": "Nested query values are sent as Python repr.",
                        "assessment": {"fix_offered_by": "someone"},
                    }
                ]
            }
        },
        [],
    )[0]
    assert "→ reproduced, @someone offered a fix: Nested query values" in line


def test_decision_without_question_or_reason_is_never_blank():
    assert outcome_lines({"bins": {"decisions": [{"issue_number": 1}]}}, [])[
        0
    ].endswith("→ needs a maintainer decision")
    assert outcome_lines(
        {"bins": {"decisions": [{"issue_number": 1, "next_step": "Pick A or B."}]}},
        [],
    )[0].endswith("→ Pick A or B.")


def live_from(items):
    return lambda number: items.get(number)


def gated(author):
    return {
        "state": "closed",
        "user": {"login": author},
        "labels": [{"name": "missing-issue-link"}],
        "pull_request": {"merged_at": None},
    }


def test_live_state_rewrites_stale_pull_request_advice():
    item = {"issue_number": 9, "verification": [{"pr_number": 5}]}
    review = {"bins": {"existing_review": [item]}}
    open_issue = {"state": "open"}

    # Closed by the issue-link gate before the outcome went out.
    live = live_from({9: open_issue, 5: gated("xDipzz")})
    assert outcome_lines(review, [], live)[0].endswith(
        "→ PR #5 is gate-closed: assign @xDipzz to reopen it, or decline"
    )
    # Merged since intake.
    merged = {"state": "closed", "pull_request": {"merged_at": "2026-11-18T00:00:00Z"}}
    live = live_from({9: open_issue, 5: merged})
    assert outcome_lines(review, [], live)[0].endswith(
        "→ PR #5 merged: close the issue"
    )
    # Still open.
    live = live_from({9: open_issue, 5: {"state": "open", "pull_request": {}}})
    assert outcome_lines(review, [], live)[0].endswith("→ review PR #5")
    # Unreadable state falls back to what intake saw.
    assert outcome_lines(review, [], live_from({}))[0].endswith("→ review PR #5")


def test_live_state_drops_issues_closed_since_intake():
    review = {"bins": {"evidence": [{"issue_number": 9}]}}
    assert outcome_lines(review, [], live_from({9: {"state": "closed"}})) == []
