import pytest

from flows.agentic_github import GitHub
from flows.agentic_intake import held, route_intake
from flows.agentic_review_publication import post_review
from tests.test_agentic_review_publication import BASE, BOT, HEAD, body

SCHEMA_BODIES = [
    f"Fixes #2692. Validate JSON Schema {version} enums."
    for version in (
        "draft-03",
        "draft-04",
        "draft-06",
        "draft-07",
        "draft-2019-09",
        "draft-2020-12",
        "draft2020",
    )
]
HOLDS = [
    {"title": "[DRAFT] Repair enums"},
    {"title": "DRAFT: Repair enums"},
    {"body": "Fixes #2692. Draft; awaiting author review."},
    {"body": "Fixes #2692. draft-07 support. DO NOT MERGE"},
    {"body": "Fixes #2692. draft-07 support. [DRAFT]"},
    {"labels": [{"name": "DNM"}]},
    {"labels": [{"name": "do-not-merge"}]},
    {"labels": [{"name": "DO_NOT_MERGE"}]},
    {"labels": [{"name": "don't merge"}]},
    {"labels": [{"name": "dont-merge"}]},
    {"labels": [{"name": "don’t merge"}]},
    {"labels": [{"name": "DRAFT"}]},
    {"draft": True},
]


def intake_pr(pr):
    return {**pr, "merged": False, "labels": [x["name"] for x in pr["labels"]]}


@pytest.mark.parametrize("description", SCHEMA_BODIES)
def test_schema_versions_reach_review_publication(evidence, endpoint, description):
    state, url, _ = endpoint
    state["pr"]["body"] = description
    evidence["prs"] = [intake_pr(state["pr"])]
    routed = route_intake(evidence)
    assert routed["route"] == "review_existing"
    assert routed["pull_requests"][0]["held"] is False
    receipt = post_review(
        GitHub("test-installation", url), 7, HEAD, BASE, BOT, body(), lambda: None
    )
    assert receipt["status"] == "posted"
    assert len(state["posts"]) == 1


@pytest.mark.parametrize("hold", HOLDS)
def test_author_holds_still_block_review_delivery(evidence, endpoint, hold):
    state, url, _ = endpoint
    state["pr"]["body"] = SCHEMA_BODIES[3]
    state["pr"].update(hold)
    pr = intake_pr(state["pr"])
    assert held(pr)
    evidence["prs"] = [pr]
    assert route_intake(evidence)["pull_requests"][0]["held"] is True
    with pytest.raises(ValueError, match="explicitly held"):
        post_review(
            GitHub("test-installation", url), 7, HEAD, BASE, BOT, body(), lambda: None
        )
    assert state["posts"] == []
