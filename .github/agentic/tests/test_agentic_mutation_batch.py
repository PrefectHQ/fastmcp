from copy import deepcopy

import pytest
from pydantic import ValidationError

from flows.agentic_mutation_batch import (
    REQUIRED,
    mutation_batch,
    qualify_recommendations,
)
from flows.agentic_mutation_models import MutationRecommendation
from flows.agentic_review_queue import retain_review_queue, review_markdown
from flows.agentic_stack import digest
from flows.agentic_stack_plans import TriageReview
from flows.agentic_triage_review import apply_disposition

HEAD, BASE = "e" * 40, "b" * 40


@pytest.fixture
def snapshot(evidence):
    return {
        **evidence,
        "stacks": [],
        "prs": [
            {
                "number": 12,
                "head_sha": HEAD,
                "base_sha": BASE,
                "state": "open",
                "draft": False,
                "merged": False,
                "title": "Repair dispatch",
                "body": f"Fixes #{evidence['issue']['number']}",
                "labels": [],
                "user": {"login": "reporter", "type": "User"},
                "comments": [],
                "timeline": [],
            }
        ],
    }


def proposal(snapshot, action="assign_issue", **updates):
    return {
        "action": action,
        "target_number": 12
        if action in {"review_pr", "finish_pr", "close_pr"}
        else snapshot["issue"]["number"],
        "related_pr": 12,
        "head_sha": HEAD,
        "base_sha": BASE,
        "evidence_sha256": digest(snapshot),
        "reason": "The supported dispatch bug has a sound scoped contribution.",
        "next_step": "Assign reporter to the issue and verify the contribution gate reruns.",
        "scope_notes": "The patch repairs dispatch and preserves explicit overrides.",
        "assignee": "reporter" if action == "assign_issue" else None,
        "norm_evidence": [
            {
                "check": check,
                "satisfied": True,
                "summary": f"Verified {check}; sparse public history is not a rejection reason.",
                "evidence_urls": ["https://github.com/PrefectHQ/fastmcp/pull/12"],
            }
            for check in sorted(REQUIRED.get(action, set()))
        ],
        **updates,
    }


def review(snapshot, action="assign_issue", **updates):
    return {
        "decision": "propose_closure"
        if action.startswith("close_") and action != "close_pr"
        else "review_existing",
        "reason": "Exact supported behavior and prior discussion reviewed.",
        "findings": [],
        "next_action": "Act on the concrete scoped proposal after approval.",
        "recommendations": [proposal(snapshot, action, **updates)],
    }


def assignment_checks(snapshot):
    return {
        "issue_number": snapshot["issue"]["number"],
        "verification": [
            {
                "pr_number": 12,
                "head_sha": HEAD,
                "baseline_sha": BASE,
                "status": "verification_recorded",
                "run_id": "executed-run",
                "tests": {"head": {"status": "passed"}},
                "comparison": {"regressed_tests": []},
            }
        ],
    }


def test_assignment_is_explicit_revision_bound_and_does_not_authorize_merge(snapshot):
    result = qualify_recommendations(
        assignment_checks(snapshot),
        review(snapshot),
        "independent-run",
        snapshot,
    )[0]
    assert result["status"] == "blocked"
    assert result["payload"]["assignee"] == "reporter"
    assert result["head_sha"] == HEAD and result["base_sha"] == BASE
    assert result["evidence_sha256"] == digest(snapshot)
    assert result["source_runs"]["triage_review"] == "independent-run"
    assert result["approval_required"] is True and result["merge_ready"] is False
    assert result["remaining_gates"] == ["platform_assignment_eligibility"]
    assert "silently ignore" in result["execution_preconditions"][-1]
    assert result["gates"]["current_ci"] == "not_applicable"


@pytest.mark.parametrize(
    "change,gate",
    [
        ("head", "target_revision"),
        ("digest", "evidence_binding"),
        ("author", "assignee_identity"),
        ("owner", "existing_ownership"),
        ("draft", "contribution_hold"),
        ("security", "open_unrestricted_issue"),
        ("history", "public_history"),
        ("scope", "scope_notes"),
    ],
)
def test_incomplete_assignment_evidence_is_blocked(snapshot, change, gate):
    output = review(snapshot)
    if change == "head":
        output["recommendations"][0]["head_sha"] = "c" * 40
    if change == "digest":
        output["recommendations"][0]["evidence_sha256"] = None
    if change == "author":
        output["recommendations"][0]["assignee"] = "someone-else"
    if change == "owner":
        snapshot["issue"]["assignees"] = ["someone-else"]
    if change == "draft":
        snapshot["prs"][0]["draft"] = True
    if change == "security":
        snapshot["issue"]["labels"] = ["security"]
    if change == "scope":
        output["recommendations"][0]["scope_notes"] = ""
    if change == "history":
        next(
            check
            for check in output["recommendations"][0]["norm_evidence"]
            if check["check"] == "public_history"
        )["satisfied"] = False
    result = qualify_recommendations({}, output, "independent-run", snapshot)[0]
    assert result["status"] == "blocked" and gate in result["remaining_gates"]


def test_gate_closed_contribution_needs_current_bot_closure_history(snapshot):
    pr = snapshot["prs"][0]
    pr["state"] = "closed"
    pr["comments"] = [{"body": "<!-- require-issue-link -->", "user": {"type": "Bot"}}]
    pr["timeline"] = [{"event": "closed", "actor": {"type": "Bot"}}]
    assert qualify_recommendations(
        assignment_checks(snapshot), review(snapshot), "run", snapshot
    )[0]["remaining_gates"] == ["platform_assignment_eligibility"]
    pr["timeline"].append({"event": "closed", "actor": {"type": "User"}})
    assert (
        "assignment_gate_history"
        in qualify_recommendations(
            assignment_checks(snapshot), review(snapshot), "run", snapshot
        )[0]["remaining_gates"]
    )


@pytest.mark.parametrize(
    "action,path", [("close_duplicate", "issues"), ("close_completed", "pulls")]
)
def test_closure_uses_real_read_only_identity_check_and_complete_scope(
    snapshot, github_server, action, path
):
    read, responses, requests = github_server
    snapshot["prs"] = []
    extra = {
        "canonical_issue" if action == "close_duplicate" else "merged_pr": 99,
        "public_comment": f"Resolved by https://github.com/PrefectHQ/fastmcp/{'pull' if path == 'pulls' else path}/99; the complete report is covered.",
    }
    responses[f"/repos/PrefectHQ/fastmcp/{path}/99"] = {
        "number": 99,
        "html_url": f"https://github.com/PrefectHQ/fastmcp/{'pull' if path == 'pulls' else path}/99",
        "state": "closed",
        "merged": True,
        "body": f"Fixes #{snapshot['issue']['number']}",
        "updated_at": "2026-10-28T00:00:00Z",
    }
    output = review(snapshot, action, **extra)
    destination, outcome = apply_disposition(
        "pruning",
        {"issue_number": snapshot["issue"]["number"]},
        output,
        "independent-run",
        snapshot,
        read,
    )
    recommendation = outcome["mutation_recommendations"][0]
    assert destination == "pruning" and recommendation["status"] == "proposed"
    assert recommendation["payload"]["public_comment"] == extra["public_comment"]
    assert len(requests) == 1 and recommendation["references"][0]["sha256"]
    output["recommendations"][0]["norm_evidence"][0]["satisfied"] = False
    assert (
        qualify_recommendations({}, output, "run", snapshot, read)[0]["status"]
        == "blocked"
    )


def test_maintainer_confirmation_can_become_a_reviewed_closure_candidate(
    snapshot, github_server
):
    read, responses, _ = github_server
    snapshot["prs"] = []
    responses["/repos/PrefectHQ/fastmcp/pulls/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/pull/99",
        "state": "closed",
        "merged": True,
        "body": f"Fixes #{snapshot['issue']['number']}",
        "updated_at": "2026-10-28T00:00:00Z",
    }
    output = review(
        snapshot,
        "close_completed",
        merged_pr=99,
        public_comment="Resolved by https://github.com/PrefectHQ/fastmcp/pull/99; the complete report is covered.",
    )
    outcome = {"issue_number": snapshot["issue"]["number"], "decision": "maintainer"}
    destination, result = apply_disposition(
        "decisions", outcome, output, "independent-run", snapshot, read
    )
    assert destination == "pruning"
    assert result["mutation_recommendations"][0]["status"] == "proposed"
    with pytest.raises(ValueError, match="concrete retained closure"):
        apply_disposition("unchanged", outcome, output, "run", snapshot, read)


def test_partial_fix_and_missing_canonical_do_not_become_closure_permission(
    snapshot, github_server
):
    read, responses, requests = github_server
    snapshot["prs"] = []
    output = review(
        snapshot,
        "close_completed",
        merged_pr=99,
        public_comment="The issue is resolved.",
    )
    responses["/repos/PrefectHQ/fastmcp/pulls/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/pull/99",
        "merged": True,
        "body": "References #2692; bytes remain unsupported.",
    }
    assert (
        "merged_complete_fix"
        in qualify_recommendations({}, output, "run", snapshot, read)[0][
            "remaining_gates"
        ]
    )
    missing = review(snapshot, "close_duplicate", public_comment="Duplicate.")
    assert (
        "canonical_identity"
        in qualify_recommendations({}, missing, "run", snapshot, read)[0][
            "remaining_gates"
        ]
    )
    assert len(requests) == 1


def test_duplicate_automation_and_human_intervention_keep_ownership(
    snapshot, github_server
):
    read, responses, _ = github_server
    snapshot["prs"] = []
    snapshot["issue"]["labels"] = ["potential-duplicate"]
    responses["/repos/PrefectHQ/fastmcp/issues/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/issues/99",
    }
    output = review(
        snapshot,
        "close_duplicate",
        canonical_issue=99,
        public_comment="Duplicate of #99.",
    )
    result = qualify_recommendations(
        {"route": {"human_intervention": True}}, output, "run", snapshot, read
    )[0]
    assert "existing_duplicate_automation_or_intervention" in result["remaining_gates"]


def test_proposal_schema_rejects_merge_and_keeps_older_review_outputs_compatible(
    snapshot,
):
    with pytest.raises(ValidationError):
        MutationRecommendation.model_validate(proposal(snapshot, action="merge_pr"))
    old = TriageReview.model_validate(
        {
            "decision": "review_existing",
            "reason": "Reviewed",
            "findings": [],
            "next_action": "Review exact head",
        }
    )
    assert old.recommendations == []
    with pytest.raises(ValueError, match="concrete retained closure"):
        apply_disposition(
            "pruning", {}, {**old.model_dump(), "decision": "propose_closure"}, "run"
        )


def test_bulk_batch_blocks_conflicts_and_stale_recommendations(snapshot):
    first = qualify_recommendations(
        assignment_checks(snapshot), review(snapshot), "run", snapshot
    )[0]
    second = deepcopy(first)
    second["id"] = "alternative"
    second["payload"]["assignee"] = "other"
    result = {
        "batch_id": "bulk",
        "bins": {"existing_review": [{"mutation_recommendations": [first, second]}]},
    }
    batch = mutation_batch(result, "owner")
    assert batch["schema_version"] == "fastmcp-mutation-batch/1"
    assert batch["approval_required"] and not batch["execution_enabled"]
    assert all(
        "conflicting_recommendations" in item["remaining_gates"]
        for item in batch["recommendations"]
    )
    result["bins"]["existing_review"][0] = {
        "status": "stale",
        "mutation_recommendations": [first],
    }
    assert (
        "fresh_evidence"
        in mutation_batch(result)["recommendations"][0]["remaining_gates"]
    )
    assert first["status"] == "blocked"
    assert first["remaining_gates"] == ["platform_assignment_eligibility"]


def test_legacy_verification_projects_concrete_repairs_without_inventing_assignments():
    item = {
        "issue_number": 1,
        "next_step": "Close the issue and assign someone",
        "verification": [
            {
                "pr_number": 12,
                "head_sha": HEAD,
                "baseline_sha": BASE,
                "run_id": "verified-run",
                "comparison": {"regressed_tests": ["test_supported_override"]},
            }
        ],
    }
    batch = mutation_batch({"batch_id": "legacy", "bins": {"existing_review": [item]}})
    assert len(batch["recommendations"]) == 1
    recommendation = batch["recommendations"][0]
    assert recommendation["action"] == "finish_pr"
    assert "test_supported_override" in recommendation["next_step"]
    assert recommendation["source_runs"]["verification"] == ["verified-run"]
    assert set(recommendation["before_merge"]) >= {
        "static_checks",
        "current_ci",
        "batch_composition",
    }


def test_mutation_artifact_is_immutable_and_queue_contains_exact_public_payload(
    snapshot,
):
    from prefect import flow
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import ArtifactFilter, FlowRunFilter
    from prefect.testing.utilities import prefect_test_harness

    @flow
    def owner_flow():
        return None

    item = {
        "issue_number": snapshot["issue"]["number"],
        "mutation_recommendations": qualify_recommendations(
            {}, review(snapshot), "review-run", snapshot
        ),
    }
    result = {"batch_id": "proposals", "bins": {"existing_review": [item]}}
    markdown = review_markdown(result)
    assert "Proposed mutations" in markdown and '"assignee": "reporter"' in markdown
    assert "No mutations are executed" in markdown
    with prefect_test_harness(), get_client(sync_client=True) as client:
        owner = client.create_flow_run(owner_flow)
        first = retain_review_queue(owner.id, result)
        assert retain_review_queue(owner.id, result) == first
        artifacts = client.read_artifacts(
            artifact_filter=ArtifactFilter(type={"any_": ["fastmcp-mutation-batch"]}),
            flow_run_filter=FlowRunFilter(id={"any_": [owner.id]}),
        )
        assert len(artifacts) == 1
        assert artifacts[0].data["recommendations"][0]["head_sha"] == HEAD
        changed = deepcopy(result)
        changed["bins"]["existing_review"][0]["mutation_recommendations"][0]["payload"][
            "assignee"
        ] = "other"
        with pytest.raises(ValueError, match="Conflicting"):
            retain_review_queue(owner.id, changed)


def consolidation(snapshot, github_server):
    read, responses, _ = github_server
    snapshot["issue"]["assignees"] = ["canonical-contributor"]
    responses["/repos/PrefectHQ/fastmcp/pulls/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/pull/99",
        "state": "open",
        "merged": False,
        "title": "The earlier maintainer-selected canonical",
        "body": "Fixes #2692",
        "head": {
            "sha": "f" * 40,
            "ref": "canonical",
            "repo": {"full_name": "PrefectHQ/fastmcp", "open_issues_count": 40},
        },
        "base": {"sha": BASE},
    }
    output = review(
        snapshot,
        "close_pr",
        canonical_pr=99,
        canonical_head_sha="f" * 40,
        canonical_base_sha=BASE,
        public_comment="Superseded by #99, the earlier maintainer-selected canonical with the same supported coverage.",
        next_step="Close this redundant PR after approval, preserving the earlier canonical and issue assignment.",
    )
    direction = next(
        check
        for check in output["recommendations"][0]["norm_evidence"]
        if check["check"] == "maintainer_direction"
    )
    direction.update(
        summary="The maintainer selected #99 in closed PR #77; newer alternatives do not replace that choice.",
        evidence_urls=["https://github.com/PrefectHQ/fastmcp/pull/77#issuecomment-123"],
    )
    outcome = {
        "issue_number": snapshot["issue"]["number"],
        "verification": [
            {
                "pr_number": number,
                "head_sha": head,
                "baseline_sha": BASE,
                "status": "verification_recorded",
                "run_id": f"verified-{number}",
                "tests": {"head": {"status": "passed"}},
                "comparison": {"regressed_tests": []},
            }
            for number, head in [(12, HEAD), (99, "f" * 40)]
        ],
    }
    return output, outcome, read, responses


def test_pr_consolidation_preserves_open_canonical_and_historical_direction(
    snapshot, github_server
):
    output, outcome, read, _ = consolidation(snapshot, github_server)
    destination, result = apply_disposition(
        "existing_review", outcome, output, "independent-run", snapshot, read
    )
    recommendation = result["mutation_recommendations"][0]
    assert destination == "existing_review" and recommendation["status"] == "proposed"
    assert recommendation["target"] == {
        "kind": "pull_request",
        "number": 12,
        "url": "https://github.com/PrefectHQ/fastmcp/pull/12",
    }
    assert recommendation["payload"]["canonical_pr"] == 99
    assert recommendation["payload"]["canonical_head_sha"] == "f" * 40
    assert set(recommendation["source_runs"]["verification"]) == {
        "verified-12",
        "verified-99",
    }
    assert not recommendation["before_merge"]
    assert recommendation["approval_required"] and not recommendation["merge_ready"]


@pytest.mark.parametrize(
    "change,gate",
    [
        ("canonical_head", "canonical_revision"),
        ("target_check", "executed_comparison"),
        ("canonical_check", "executed_comparison"),
        ("canonical_failure", "canonical_behavior"),
        ("maintainer_direction", "maintainer_direction"),
        ("parity", "coverage_parity"),
        ("hold", "contribution_hold"),
    ],
)
def test_pr_consolidation_requires_both_revisions_and_skill_evidence(
    snapshot, github_server, change, gate
):
    output, outcome, read, responses = consolidation(snapshot, github_server)
    if change == "canonical_head":
        responses["/repos/PrefectHQ/fastmcp/pulls/99"]["head"]["sha"] = "c" * 40
    if change == "target_check":
        outcome["verification"] = outcome["verification"][1:]
    if change == "canonical_check":
        outcome["verification"] = outcome["verification"][:1]
    if change == "canonical_failure":
        outcome["verification"][1]["tests"]["head"]["status"] = "failed"
    if change == "maintainer_direction":
        next(
            check
            for check in output["recommendations"][0]["norm_evidence"]
            if check["check"] == "maintainer_direction"
        )["satisfied"] = False
    if change == "parity":
        next(
            check
            for check in output["recommendations"][0]["norm_evidence"]
            if check["check"] == "coverage_parity"
        )["satisfied"] = False
    if change == "hold":
        snapshot["prs"][0]["labels"] = ["do-not-merge"]
    recommendation = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    assert (
        gate in recommendation["remaining_gates"]
        and recommendation["status"] == "blocked"
    )


def test_resolution_fingerprint_ignores_repository_counters_but_detects_meaningful_changes(
    snapshot, github_server
):
    output, outcome, read, responses = consolidation(snapshot, github_server)
    first = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    responses["/repos/PrefectHQ/fastmcp/pulls/99"]["head"]["repo"][
        "open_issues_count"
    ] += 1
    second = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    assert first["references"] == second["references"] and first["id"] == second["id"]
    responses["/repos/PrefectHQ/fastmcp/pulls/99"]["body"] += (
        "\nMaintainer: coverage is incomplete"
    )
    changed = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    assert changed["references"] != first["references"] and changed["id"] != first["id"]


def test_duplicate_norm_checks_are_rejected_and_model_budget_is_bounded(snapshot):
    output = review(snapshot)
    output["recommendations"] *= 4
    with pytest.raises(ValidationError):
        TriageReview.model_validate(output)
    one = proposal(snapshot)
    one["norm_evidence"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        MutationRecommendation.model_validate(one)


def test_assignment_needs_executed_head_evidence_in_addition_to_public_history(
    snapshot,
):
    result = qualify_recommendations({}, review(snapshot), "run", snapshot)[0]
    assert "executed_verification" in result["remaining_gates"]
    checked = assignment_checks(snapshot)
    checked["verification"][0]["head_sha"] = "c" * 40
    assert (
        "executed_verification"
        in qualify_recommendations(checked, review(snapshot), "run", snapshot)[0][
            "remaining_gates"
        ]
    )


def test_one_existing_work_batch_can_assign_canonical_and_close_two_alternatives(
    snapshot, github_server
):
    read, responses, requests = github_server
    snapshot["prs"][0]["user"]["login"] = "alternative-a"
    canonical = {
        **deepcopy(snapshot["prs"][0]),
        "number": 99,
        "head_sha": "f" * 40,
        "user": {"login": "reporter", "type": "User"},
    }
    alternative = {
        **deepcopy(snapshot["prs"][0]),
        "number": 13,
        "head_sha": "d" * 40,
        "user": {"login": "alternative-b", "type": "User"},
    }
    snapshot["prs"].extend([canonical, alternative])
    responses["/repos/PrefectHQ/fastmcp/pulls/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/pull/99",
        "state": "open",
        "merged": False,
        "body": "Fixes #2692",
        "head": {"sha": "f" * 40},
        "base": {"sha": BASE},
    }
    assign = proposal(snapshot, related_pr=99, head_sha="f" * 40)
    close = proposal(
        snapshot,
        "close_pr",
        canonical_pr=99,
        canonical_head_sha="f" * 40,
        public_comment="Superseded by the maintainer-selected #99 with equivalent supported coverage.",
    )
    other_close = {**deepcopy(close), "target_number": 13, "head_sha": "d" * 40}
    output = {**review(snapshot), "recommendations": [assign, close, other_close]}
    TriageReview.model_validate(output)
    outcome = {
        "issue_number": snapshot["issue"]["number"],
        "verification": [
            {
                "pr_number": pr["number"],
                "head_sha": pr["head_sha"],
                "baseline_sha": BASE,
                "status": "verification_recorded",
                "run_id": f"verified-{pr['number']}",
                "tests": {"head": {"status": "passed"}},
                "comparison": {"regressed_tests": []},
            }
            for pr in snapshot["prs"]
        ],
    }
    destination, resolved = apply_disposition(
        "existing_review", outcome, output, "run", snapshot, read
    )
    assert destination == "existing_review"
    batch = mutation_batch({"batch_id": "mixed", "bins": {destination: [resolved]}})
    assert len(batch["recommendations"]) == 3
    assignment = next(
        item for item in batch["recommendations"] if item["action"] == "assign_issue"
    )
    assert assignment["remaining_gates"] == ["platform_assignment_eligibility"]
    closes = [item for item in batch["recommendations"] if item["action"] == "close_pr"]
    assert all(
        item["status"] == "proposed" and not item["remaining_gates"] for item in closes
    )
    assert len(requests) == 1


def test_mutation_batch_carries_exact_acknowledged_review_receipts():
    receipt = {
        "pr_number": 12,
        "head_sha": HEAD,
        "review_id": 5,
        "reviewer_login": "reviewer[bot]",
        "body_sha256": "a" * 64,
    }
    result = {
        "batch_id": "ack",
        "bins": {
            "existing_review": [
                {
                    "issue_number": 1,
                    "published_reviews": [receipt],
                    "verification": [
                        {
                            "pr_number": 12,
                            "head_sha": HEAD,
                            "baseline_sha": BASE,
                            "run_id": "executed",
                        }
                    ],
                }
            ]
        },
    }
    assert mutation_batch(result)["recommendations"][0]["acknowledged_reviews"] == [
        receipt
    ]


@pytest.mark.parametrize(
    "canonical_state,merged,blocked",
    [("open", False, False), ("closed", False, True), ("closed", True, False)],
)
def test_closed_unmerged_canonical_requires_reopen_coordination(
    snapshot, github_server, canonical_state, merged, blocked
):
    output, outcome, read, responses = consolidation(snapshot, github_server)
    canonical = responses["/repos/PrefectHQ/fastmcp/pulls/99"]
    canonical.update(state=canonical_state, merged=merged)
    if merged:
        output["recommendations"][0]["norm_evidence"].append(
            {
                "check": "current_fix_and_reverts",
                "satisfied": True,
                "summary": "The selected fix remains present on the pinned main baseline.",
                "evidence_urls": ["https://github.com/PrefectHQ/fastmcp/pull/99"],
            }
        )
    result = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    assert ("canonical_reopen_coordination" in result["remaining_gates"]) is blocked
    assert (result["status"] == "blocked") is blocked


@pytest.mark.parametrize("kind", ["issue", "pull_request"])
def test_bulk_canonical_cycles_do_not_lose_both_trackers(snapshot, github_server, kind):
    read, responses, _ = github_server
    action = "close_duplicate" if kind == "issue" else "close_pr"
    rows = []
    for number, other in [(12, 99), (99, 12)]:
        current = deepcopy(snapshot)
        if kind == "issue":
            current["issue"]["number"] = number
            current["prs"] = []
            responses[f"/repos/PrefectHQ/fastmcp/issues/{other}"] = {
                "number": other,
                "html_url": f"https://github.com/PrefectHQ/fastmcp/issues/{other}",
                "state": "open",
            }
            output = review(
                current,
                action,
                canonical_issue=other,
                public_comment=f"Duplicate of #{other}.",
            )
            result = qualify_recommendations({}, output, "run", current, read)[0]
        else:
            output, outcome, _, responses = consolidation(current, github_server)
            current["prs"][0]["number"] = number
            current["prs"][0]["head_sha"] = HEAD
            output["recommendations"][0].update(
                target_number=number,
                canonical_pr=other,
                evidence_sha256=digest(current),
                public_comment=f"Superseded by #{other}.",
            )
            responses[f"/repos/PrefectHQ/fastmcp/pulls/{other}"] = {
                "number": other,
                "html_url": f"https://github.com/PrefectHQ/fastmcp/pull/{other}",
                "state": "open",
                "merged": False,
                "head": {"sha": "f" * 40},
                "base": {"sha": BASE},
            }
            outcome["verification"] = [
                {
                    "pr_number": number,
                    "head_sha": HEAD,
                    "status": "verification_recorded",
                    "run_id": "target",
                    "tests": {"head": {"status": "passed"}},
                },
                {
                    "pr_number": other,
                    "head_sha": "f" * 40,
                    "status": "verification_recorded",
                    "run_id": "canonical",
                    "tests": {"head": {"status": "passed"}},
                },
            ]
            result = qualify_recommendations(outcome, output, "run", current, read)[0]
        assert result["status"] == "proposed"
        rows.append({"issue_number": number, "mutation_recommendations": [result]})
    batch = mutation_batch({"batch_id": "cycle", "bins": {"pruning": rows}})
    assert len(batch["recommendations"]) == 2
    assert all(
        "canonical_survival_or_assignment_conflict" in item["remaining_gates"]
        and item["status"] == "blocked"
        for item in batch["recommendations"]
    )


def test_assigning_a_contribution_that_the_same_batch_closes_is_blocked(
    snapshot, github_server
):
    output, outcome, read, _ = consolidation(snapshot, github_server)
    close = qualify_recommendations(outcome, output, "run", snapshot, read)[0]
    assign = qualify_recommendations(
        assignment_checks(snapshot), review(snapshot), "run", snapshot
    )[0]
    batch = mutation_batch(
        {
            "batch_id": "conflict",
            "bins": {
                "existing_review": [{"mutation_recommendations": [close, assign]}]
            },
        }
    )
    assert all(
        "canonical_survival_or_assignment_conflict" in item["remaining_gates"]
        for item in batch["recommendations"]
    )


def test_native_prune_without_route_preserves_human_reopening(snapshot, github_server):
    from flows.agentic_intake import route_intake

    read, responses, _ = github_server
    snapshot["prs"] = []
    snapshot["timeline"] = [{"event": "reopened", "created_at": "2026-10-28T00:00:00Z"}]
    assert route_intake(snapshot)["human_intervention"] is True
    responses["/repos/PrefectHQ/fastmcp/issues/99"] = {
        "number": 99,
        "html_url": "https://github.com/PrefectHQ/fastmcp/issues/99",
        "state": "open",
    }
    native = {
        "issue_number": snapshot["issue"]["number"],
        "decision": "prune",
        "reason": "Potential duplicate",
    }
    output = review(
        snapshot,
        "close_duplicate",
        canonical_issue=99,
        public_comment="Duplicate of #99.",
    )
    destination, result = apply_disposition(
        "pruning", native, output, "independent-run", snapshot, read
    )
    assert destination == "pruning"
    recommendation = result["mutation_recommendations"][0]
    assert recommendation["status"] == "blocked"
    assert (
        "existing_duplicate_automation_or_intervention"
        in recommendation["remaining_gates"]
    )
