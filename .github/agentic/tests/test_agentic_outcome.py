import subprocess
from uuid import uuid4

import pytest
from pydantic import ValidationError

import flows.agentic_triage as triage
from flows.agentic_backlog import Intake
from flows.agentic_outcome import outcome_lines, subjects
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
        f"<{URL}6303|#6303> A proxy served over stdio forwards only some of the"
        " backend's `ctx.lo… · @alpaylan → review PR #6309",
        f"<{URL}6316|#6316> fastmcp-tasks: let TasksExtension run a custom docket"
        " Worker · @rob-archivium → Should FastMCP add and maintain a supported"
        " TasksExtension customization seam for alternate Docket…",
        f"<{URL}6295|#6295> Redirect &lt;URI&gt; &amp; helpers · @alpaylan →"
        " Security-labeled report requires restricted review",
        f"<{URL}6344|#6344> Add CrakSEO MCP connect skills → close? The request is"
        " promotional rather than a FastMCP defect.",
        f"<{URL}6400|#6400> → needs repro info",
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
    "review,emitted",
    [
        (REVIEW, True),
        ({"bins": {"unchanged": REVIEW["bins"]["unchanged"]}}, False),
        ({"bins": {"operational": REVIEW["bins"]["operational"]}}, False),
        (None, False),
    ],
)
def test_announce_emits_only_when_a_maintainer_is_needed(monkeypatch, review, emitted):
    calls = []
    monkeypatch.setattr(triage, "emit_event", lambda **event: calls.append(event))
    owner = uuid4()
    announce(owner, {"review": review, "issues": ISSUES})
    if not emitted:
        assert calls == []
        return
    assert calls == [
        {
            "event": "fastmcp.triage.outcome",
            "resource": {"prefect.resource.id": f"fastmcp.triage.intake.{owner}"},
            "payload": {"text": "\n".join(outcome_lines(REVIEW, ISSUES))},
        }
    ]


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
