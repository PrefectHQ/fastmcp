from copy import deepcopy

import pytest

from flows.agentic_freshness import change_kind, revalidate
from flows.agentic_review_publication import before_delivery, post_review
from flows.agentic_stack_policy import publication_revision, review_revision
from flows.agentic_stack_publication import GitHub
from tests.test_agentic_review_publication import BASE, BOT, HEAD, body


@pytest.fixture
def snapshot(evidence):
    return {
        **evidence,
        "stacks": [],
        "base_sha": BASE,
        "prs": [
            {
                "number": 7,
                "title": "Repair defaults",
                "body": "Fixes #2692",
                "state": "open",
                "draft": False,
                "merged": False,
                "head_sha": HEAD,
                "base_sha": BASE,
                "head_repository": "contributor/fastmcp",
                "labels": [],
                "assignees": [],
                "comments": [],
                "review_comments": [],
                "reviews": [],
                "timeline": [],
                "updated_at": "before",
                "checks": [{"id": 1, "status": "in_progress"}],
                "statuses": [],
            }
        ],
    }


def test_ci_progress_preserves_review_but_not_write_snapshot(snapshot):
    current = deepcopy(snapshot)
    current["prs"][0].update(
        updated_at="after",
        checks=[{"id": 1, "status": "completed", "conclusion": "success"}],
        statuses=[{"id": 2, "state": "success"}],
    )
    assert change_kind(snapshot, current) == "unchanged"
    assert change_kind(snapshot, current, for_review=False) == "stale"
    assert publication_revision(snapshot) != publication_revision(current)
    assert snapshot["prs"][0]["checks"] == [{"id": 1, "status": "in_progress"}]
    result = {"bins": {"stacks": [{"issue_number": 2692}], "operational": []}}
    refreshed = revalidate(result, {2692: snapshot}, {2692: "main"}, lambda *_: current)
    assert refreshed["bins"]["operational"][0]["status"] == "stale"


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "c" * 40),
        ("base_sha", "c" * 40),
        ("head_repository", "another/fastmcp"),
        ("state", "closed"),
        ("merged", True),
        ("draft", True),
        ("title", "[DNM] Repair defaults"),
        ("body", "Do not merge: supported override is broken"),
        ("labels", ["do-not-merge"]),
        ("assignees", ["contributor"]),
        ("comments", [{"id": 3, "body": "Maintainer changed the contract"}]),
        ("review_comments", [{"id": 4, "body": "Supported behavior regresses"}]),
        ("reviews", [{"id": 5, "state": "CHANGES_REQUESTED"}]),
        ("timeline", [{"id": 6, "event": "review_dismissed"}]),
    ],
)
def test_consequential_pr_changes_remain_stale(snapshot, field, value):
    current = deepcopy(snapshot)
    current["prs"][0][field] = value
    assert change_kind(snapshot, current) == "stale"


@pytest.mark.parametrize("field", ["body", "labels", "assignees", "state"])
def test_issue_changes_remain_stale(snapshot, field):
    current = deepcopy(snapshot)
    current["issue"][field] = "changed"
    assert change_kind(snapshot, current) == "stale"


@pytest.mark.parametrize("scope", ["issue", "pr"])
@pytest.mark.parametrize("repository", ["someone/monitor", "PrefectHQ/fastmcp", None])
def test_only_demonstrably_foreign_cross_references_are_ignored(
    snapshot, scope, repository
):
    current = deepcopy(snapshot)
    source = {"number": 99}
    if repository:
        source.update(
            repository={"full_name": repository},
            html_url=f"https://github.com/{repository}/issues/99",
        )
    events = current["timeline"] if scope == "issue" else current["prs"][0]["timeline"]
    events.append({"event": "cross-referenced", "source": {"issue": source}})
    expected = "unchanged" if repository == "someone/monitor" else "stale"
    assert change_kind(snapshot, current) == expected


def test_real_review_post_does_not_invalidate_itself_through_actions(
    endpoint, snapshot
):
    state, url, _ = endpoint
    current = deepcopy(snapshot)

    def guard():
        current["prs"][0]["checks"][0].update(status="completed", conclusion="success")
        assert review_revision(current) == review_revision(snapshot)

    receipt = post_review(
        GitHub("test-installation", url), 7, HEAD, BASE, BOT, body(), guard
    )
    current["prs"][0].update(updated_at="after", reviews=state["reviews"])
    current["prs"][0]["checks"].append(
        {"id": 2, "name": "minimize", "status": "completed", "conclusion": "skipped"}
    )
    result = {
        "bins": {
            "existing_review": [{"issue_number": 2692, "published_reviews": [receipt]}],
            "operational": [],
        }
    }
    assert (
        revalidate(result, {2692: snapshot}, {2692: "main"}, lambda *_: current)
        == result
    )
    current["prs"][0]["review_comments"].append(
        {"id": 4, "body": "New substantive bot finding", "user": {"type": "Bot"}}
    )
    assert (
        revalidate(result, {2692: snapshot}, {2692: "main"}, lambda *_: current)[
            "bins"
        ]["operational"][0]["status"]
        == "stale"
    )
    assert len(state["reviews"]) == 1
    assert before_delivery(current, [receipt], BOT)["prs"][0]["reviews"] == []
