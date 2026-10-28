from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from prefect import flow
from prefect.client.orchestration import get_client
from prefect.states import Running
from prefect.testing.utilities import prefect_test_harness

from flows.agentic_backlog import settle
from flows.agentic_baseline import refresh_context, review_existing
from flows.agentic_freshness import change_kind, revalidate
from flows.agentic_intake import route_intake
from flows.agentic_stack import digest
from flows.agentic_stack_policy import Batch
from flows.agentic_stack_runtime import AgentPlan, Runtime, record, saved


@pytest.fixture
def review_item(evidence):
    evidence = {
        **evidence,
        "stacks": [],
        "prs": [
            {
                "number": 2,
                "body": "Fixes #2692",
                "title": "Fix hydration",
                "state": "open",
                "draft": False,
                "merged": False,
                "head_sha": "c" * 40,
                "base_sha": "a" * 40,
                "base_ref": "main",
                "head_repository": "PrefectHQ/fastmcp",
                "comments": [],
            }
        ],
    }
    return {
        "evidence": evidence,
        "route": route_intake(evidence),
        "group_rationale": "Review existing contributions",
        "assessment": {
            "decision": "existing_review",
            "reason": "Fixes hydration",
            "contract": "Preserve type",
            "prior_art": [],
            "question": "",
        },
    }


def comparison_response(base, head):
    return {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "commits": [
            {"sha": head, "commit": {"message": "Update an unrelated example"}}
        ],
        "files": [
            {
                "filename": "docs/example.mdx",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n-old\n+new",
            }
        ],
    }


def configure_compare(responses, base, head, value=None):
    responses[f"/repos/PrefectHQ/fastmcp/compare/{base}...{head}?per_page=100"] = (
        value or comparison_response(base, head)
    )


def test_baseline_refresh_preserves_history_and_only_omits_repeated_discussion(
    review_item, github_server
):
    read, responses, _ = github_server
    current = {**review_item["evidence"], "base_sha": "b" * 40}
    configure_compare(responses, "a" * 40, "b" * 40)
    before = deepcopy(review_item)
    context = refresh_context(review_item, current, read)
    assert context["previous_assessment"] == review_item["assessment"]
    assert context["evidence"]["base_sha"] == "b" * 40
    assert context["route"] == route_intake(current)
    assert context["baseline_change"]["complete"]
    assert not context["baseline_change"]["tests_executed"]
    assert "omits their repeated text" in context["context_policy"]
    assert review_item == before


@pytest.mark.parametrize("change", ["reply", "head", "hold", "target", "closed"])
def test_contribution_changes_cannot_use_baseline_refresh(review_item, change):
    current = deepcopy(review_item["evidence"])
    current["base_sha"] = "b" * 40
    if change == "reply":
        current["comments"].append({"body": "Maintainer correction"})
    elif change == "head":
        current["prs"][0]["head_sha"] = "d" * 40
    elif change == "hold":
        current["prs"][0]["draft"] = True
    elif change == "target":
        current["prs"][0]["base_ref"] = "release/3.x"
    else:
        current["issue"]["state"] = "closed"
    assert change_kind(review_item["evidence"], current) == "stale"
    with pytest.raises(ValueError, match="unchanged contribution"):
        refresh_context(
            review_item, current, lambda _: pytest.fail("No compare should be fetched")
        )


@pytest.mark.parametrize(
    "change",
    ["diverged", "behind", "missing_patch", "commits_truncated", "large_message"],
)
def test_incomplete_or_nonforward_history_cannot_support_refresh(
    review_item, github_server, change
):
    read, responses, _ = github_server
    value = comparison_response("a" * 40, "b" * 40)
    if change == "diverged":
        value["status"] = "diverged"
    elif change == "behind":
        value["behind_by"] = 1
    elif change == "missing_patch":
        del value["files"][0]["patch"]
    elif change == "commits_truncated":
        value["total_commits"] = 2
    else:
        value["commits"][0]["commit"]["message"] = "x" * 64000
    configure_compare(responses, "a" * 40, "b" * 40, value)
    with pytest.raises(ValueError, match="complete bounded fast-forward"):
        refresh_context(
            review_item, {**review_item["evidence"], "base_sha": "b" * 40}, read
        )


def test_baseline_churn_remains_pending_and_yields_to_other_work(review_item):
    evidence = review_item["evidence"]
    result = {"bins": {"existing_review": [{"issue_number": 2692}], "operational": []}}
    changed = revalidate(
        result,
        {2692: evidence},
        {2692: "main"},
        lambda *_: {**evidence, "base_sha": "b" * 40},
    )
    assert changed["bins"]["operational"][0]["status"] == "baseline_changed"
    pending = [
        {"issue_number": 2692, "attempts": 2},
        {"issue_number": 7, "attempts": 0},
    ]
    for _ in range(4):
        pending, completed = settle(pending, [2692], changed)
        assert completed == []
    assert pending == [
        {"issue_number": 7, "attempts": 0},
        {"issue_number": 2692, "attempts": 2},
    ]


@flow
def owner_flow():
    pass


@pytest.mark.parametrize("keeps_moving", [False, True])
def test_durable_refresh_resumes_and_never_exceeds_two_assessments(
    review_item, github_server, keeps_moving
):
    read, responses, requests = github_server
    now = datetime.now(UTC)
    batch = Batch.model_validate(
        {
            "id": "refresh",
            "started_at": now,
            "expires_at": now + timedelta(hours=2),
            "groups": [
                {
                    "name": "triage",
                    "rationale": "Review",
                    "branch": "main",
                    "base_sha": "a" * 40,
                    "items": [{"issue_number": 2692, "mode": "mre"}],
                }
            ],
        }
    )
    group = batch.groups[0]
    runtime = Runtime(
        plans={
            role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
            for role in ("assess", "reproduce", "prepare", "review")
        },
        validator_id=uuid4(),
        source_sha="f" * 40,
    )

    def load_skills(sha):
        return {
            "existing_instructions": {
                "base_sha": sha,
                "files": {
                    "rules": {"sha256": "e" * 64, "content": "Retain human holds"}
                },
            }
        }

    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(owner_flow, state=Running()).id
        item = deepcopy(review_item)
        for attempt, (base, head) in enumerate(
            [("a", "b"), ("b", "c" if keeps_moving else "b"), ("c", "d")][
                : 3 if keeps_moving else 2
            ]
        ):
            stage = "initial" if attempt == 0 else f"refresh-{attempt}"
            current = {**item["evidence"], "base_sha": head * 40}
            target = group.model_copy(update={"base_sha": base * 40})
            binding = digest(
                [
                    target.model_dump(mode="json"),
                    runtime.model_dump(mode="json"),
                    item,
                    {},
                ]
            )
            review = {
                "issue_number": 2692,
                "assessment": item["assessment"],
                "route": item["route"],
                "verification": [
                    {"status": "verification_recorded", "baseline_sha": base * 40}
                ],
                "status": "baseline_changed"
                if base != head
                else "verification_recorded",
                "merge_ready": False,
            }
            record.fn(
                owner,
                f"refresh-issue-2692-verify-{stage}-outcome",
                {
                    "binding": binding,
                    "outcome": {"review": review, "observed": current},
                },
            )
            configure_compare(responses, base * 40, head * 40)
            item = {**item, "evidence": current, "route": route_intake(current)}
        calls = []

        def interrupted(*_):
            raise RuntimeError("Transport interrupted before assessment response")

        args = (
            owner,
            batch,
            group,
            runtime,
            review_item,
            {},
            load_skills("a" * 40)["existing_instructions"],
        )
        with pytest.raises(RuntimeError, match="Transport interrupted"):
            review_existing(*args, interrupted, load_skills, read)

        def assess(role, instructions, context, key):
            calls.append(key)
            assert context["baseline_change"]["head_sha"] == instructions["base_sha"]
            return review_item["assessment"], str(uuid4())

        destination, result = review_existing(*args, assess, load_skills, read)
        assert destination == "existing_review"
        assert len(calls) == (2 if keeps_moving else 1)
        assert len(requests) == len(calls)
        accepted = saved.fn(owner, "refresh-issue-2692-review-evidence")
        assert accepted["base_sha"] == ("c" if keeps_moving else "b") * 40
        assert result["status"] == (
            "baseline_changed" if keeps_moving else "verification_recorded"
        )
        assert not result["merge_ready"]
