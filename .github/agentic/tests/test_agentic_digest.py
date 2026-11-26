from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

import flows.agentic_digest as digest
from flows.agentic_stack_plans import OUTPUTS, STANDALONE_ROLES, Digest


def test_digest_plan_reads_the_config_key():
    flow_id, version_id = uuid4(), uuid4()
    plan = digest.digest_plan(
        {"digest": {"flow_id": str(flow_id), "version_id": str(version_id)}}
    )
    assert plan is not None
    assert (plan.flow_id, plan.version_id) == (flow_id, version_id)
    assert digest.digest_plan({}) is None
    assert digest.digest_plan({"digest": "nope"}) is None
    assert digest.digest_plan(None) is None


def test_waiting_reads_the_triage_queue_and_drops_pull_requests():
    paths = []

    def pages(path):
        paths.append(path)
        return [{"number": 1}, {"number": 2, "pull_request": {}}]

    assert digest.waiting(pages) == [{"number": 1}]
    assert paths == [
        "repos/PrefectHQ/fastmcp/issues?state=open&labels=needs%3Atriage"
        "&sort=created&direction=asc"
    ]


def test_queue_entry_carries_the_recorded_next_step(monkeypatch):
    keys = []

    def artifacts(key, limit):
        keys.append((key, limit))
        return [{"line": "Review #6309 against the repro."}]

    monkeypatch.setattr(digest, "artifacts", artifacts)
    item = {
        "number": 6303,
        "title": "Tasks break on Valkey",
        "user": {"login": "reporter"},
        "labels": [{"name": "server"}, {"name": "needs:triage"}],
        "created_at": "2026-11-20T08:00:00Z",
        "comments": 4,
    }
    entry = digest.queue_entry(item, datetime(2026, 11, 23, 9, tzinfo=UTC))
    assert entry == {
        "number": 6303,
        "title": "Tasks break on Valkey",
        "author": "reporter",
        "labels": ["needs:triage", "server"],
        "age_days": 3,
        "comments": 4,
        "next_step": "Review #6309 against the repro.",
    }
    assert keys == [("fastmcp-triage-next-6303", 1)]


def test_queue_entry_without_a_recorded_step(monkeypatch):
    monkeypatch.setattr(digest, "artifacts", lambda key, limit: [])
    item = {"number": 7, "created_at": "2026-11-23T00:00:00Z", "user": None}
    entry = digest.queue_entry(item, datetime(2026, 11, 23, 9, tzinfo=UTC))
    assert entry["next_step"] is None
    assert entry["author"] is None
    assert entry["labels"] == []


def test_digest_is_a_standalone_role():
    assert OUTPUTS["digest"] is Digest
    assert "digest" in STANDALONE_ROLES
    with pytest.raises(ValidationError):
        Digest.model_validate({"post": True, "text": "x" * 3001, "why": "too long"})
    with pytest.raises(ValidationError):
        Digest.model_validate({"post": False, "text": "", "why": ""})


NOW = datetime(2026, 11, 26, 18, tzinfo=UTC)


def _pull(number, sender, body, closed_at="2026-11-25T20:00:00Z", **extra):
    return {
        "number": number,
        "title": f"PR {number}",
        "body": body,
        "user": {"login": sender, "type": "User"},
        "author_association": "CONTRIBUTOR",
        "labels": [{"name": "missing-issue-link"}],
        "merged_at": None,
        "closed_at": closed_at,
        "updated_at": closed_at,
        **extra,
    }


def _issue(number, **extra):
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": "open",
        "assignees": [],
        "user": {"login": "reporter"},
        **extra,
    }


def _reader(pulls, issues, comments=None):
    comments = comments or {}

    def read(path):
        if path.startswith("repos/PrefectHQ/fastmcp/pulls?state=closed"):
            return pulls if path.endswith("page=1") else []
        if "/comments" in path:
            return comments.get(int(path.split("/")[-2]), [])
        return issues.get(int(path.rsplit("/", 1)[1]))

    return read


@pytest.fixture
def steps(monkeypatch):
    lines: dict[int, str] = {}

    def artifacts(key, limit):
        number = int(key.rsplit("-", 1)[1])
        return [{"line": lines[number]}] if number in lines else []

    monkeypatch.setattr(digest, "artifacts", artifacts)
    return lines


def test_gate_closed_contribution_to_an_unassigned_issue_waits_on_a_maintainer(steps):
    steps[6820] = "reproduced: Prepare a focused fix"
    read = _reader([_pull(6821, "Pipe5linger", "Fixes #6820")], {6820: _issue(6820)})
    assert digest.gate_closed(read, None, NOW) == [
        {
            "sender": "Pipe5linger",
            "standing": "unknown",
            "count": 1,
            "prs": [
                {
                    "pr": 6821,
                    "title": "PR 6821",
                    "closed_at": "2026-11-25T20:00:00Z",
                    "issue": 6820,
                    "issue_title": "Issue 6820",
                    "issue_author": "reporter",
                    "next_step": "reproduced: Prepare a focused fix",
                    "reproduced": True,
                }
            ],
        }
    ]


@pytest.mark.parametrize(
    "pull, issue",
    [
        # assigning anyone decides it: the author's PR reopens, anyone else's stays closed
        (_pull(1, "a", "Fixes #10"), _issue(10, assignees=[{"login": "x"}])),
        (_pull(1, "a", "Fixes #10"), _issue(10, state="closed")),
        (_pull(1, "a", "Related to #10"), _issue(10)),
        (_pull(1, "a", "Fixes #10", merged_at="2026-11-25T20:00:00Z"), _issue(10)),
        (_pull(1, "a", "Fixes #10", labels=[]), _issue(10)),
        (_pull(1, "a", "Fixes #10", author_association="MEMBER"), _issue(10)),
        (_pull(1, "a", "Fixes #10", closed_at="2026-10-01T00:00:00Z"), _issue(10)),
    ],
)
def test_gate_closed_leaves_out_what_is_decided_or_not_the_gates(steps, pull, issue):
    assert digest.gate_closed(_reader([pull], {10: issue}), None, NOW) == []


def test_a_maintainer_answer_after_the_close_counts_as_a_decision(steps):
    pull = _pull(1, "a", "Fixes #10")
    before = {
        "author_association": "MEMBER",
        "user": {"type": "User"},
        "created_at": "2026-11-25T19:00:00Z",
    }
    after = {**before, "created_at": "2026-11-25T21:00:00Z"}
    bot = {**after, "user": {"type": "Bot"}}
    issues = {10: _issue(10)}
    assert digest.gate_closed(_reader([pull], issues, {1: [before, bot]}), None, NOW)
    assert digest.gate_closed(_reader([pull], issues, {1: [after]}), None, NOW) == []


def test_gate_closed_ranks_reproduced_then_vouched_then_recent(steps):
    steps[11] = "reproduced: fix it"
    pulls = [
        _pull(1, "recent", "Fixes #10", closed_at="2026-11-25T23:00:00Z"),
        _pull(2, "older", "Fixes #12", closed_at="2026-11-24T00:00:00Z"),
        _pull(3, "vouched", "Fixes #10", closed_at="2026-11-20T00:00:00Z"),
        _pull(4, "denounced", "Fixes #10", closed_at="2026-11-25T23:30:00Z"),
        _pull(5, "repro", "Fixes #11", closed_at="2026-11-19T00:00:00Z"),
        _pull(6, "older", "Fixes #11", closed_at="2026-11-23T00:00:00Z"),
    ]
    issues = {n: _issue(n) for n in (10, 11, 12)}
    known = {"vouched": "vouched", "denounced": "denounced"}

    def standing(login):
        return {"standing": known.get(login, "unknown")}

    entries = digest.gate_closed(_reader(pulls, issues), standing, NOW)
    assert [entry["sender"] for entry in entries] == [
        "older",  # has a reproduced issue, more recent than "repro"
        "repro",
        "vouched",
        "recent",
        "denounced",
    ]
    assert [pr["pr"] for pr in entries[0]["prs"]] == [6, 2]
