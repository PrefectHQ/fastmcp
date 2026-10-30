import pytest

from flows.agentic_intake import held, route_intake
from flows.agentic_review_publication import post_review
from flows.agentic_stack import StackLayer, StackSpec
from flows.agentic_stack_publication import GitHub, pr_body, verify_pr
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


@pytest.mark.parametrize("description", SCHEMA_BODIES)
def test_schema_versions_do_not_prevent_verifying_prepared_draft(description):
    layer = StackLayer(
        issue_number=2692,
        patch="patch",
        title="Repair enums",
        body=description,
        credits=[],
        evidence_revision="c" * 64,
    )
    spec = StackSpec(
        batch_id="batch",
        group="schema",
        trunk_ref="main",
        trunk_sha=BASE,
        created_at="2026-10-28T00:00:00Z",
        publisher_login="automation",
        publisher_id=10,
        layers=[layer],
    )
    manifest = {
        "branch": spec.branch(layer),
        "head_sha": HEAD,
        "base_ref": "main",
        "parent_sha": BASE,
    }
    pr = {
        "state": "open",
        "draft": True,
        "merged_at": None,
        "title": layer.title,
        "body": pr_body(layer),
        "user": {"id": 10},
        "labels": [],
        "head": {
            "ref": manifest["branch"],
            "sha": HEAD,
            "repo": {"full_name": "PrefectHQ/fastmcp"},
        },
        "base": {
            "ref": "main",
            "sha": BASE,
            "repo": {"full_name": "PrefectHQ/fastmcp"},
        },
    }
    verify_pr(pr, spec, layer, manifest)
    pr["labels"] = [{"name": "DNM"}]
    with pytest.raises(ValueError, match="Explicit hold"):
        verify_pr(pr, spec, layer, manifest)
