import json
from pathlib import Path
from uuid import uuid4

import pytest

from flows.agentic_stack_plans import build_plans
from flows.agentic_stack_runtime import AgentPlan, Runtime
from flows.agentic_triage_review import apply_disposition, requires_review


@pytest.mark.parametrize(
    ("decision", "destination"),
    [
        ("maintainer", "decisions"),
        ("skip", "unchanged"),
        ("repair_evidence", "evidence"),
    ],
)
def test_disposition_routes_automatic_triage_to_its_action_queue(decision, destination):
    review = {
        "decision": decision,
        "reason": "Supported finding",
        "next_action": "Resolve this specific finding",
        "findings": [],
    }
    category, result = apply_disposition(
        "evidence", {"issue_number": 1, "decision": "reproduced"}, review, "review-run"
    )
    assert category == destination
    assert result["triage_review_run_id"] == "review-run"
    if decision == "repair_evidence":
        assert result["decision"] == "evidence_needed"


def test_prepare_disposition_carries_scope_controls_to_preparation():
    review = {
        "decision": "prepare_fix",
        "reason": "Worthwhile scoped fix",
        "next_action": "Preserve masking and rate limits while repairing formatting",
        "findings": ["Include shared resource controls when implementing"],
    }
    category, _ = apply_disposition(
        "evidence", {"decision": "reproduced"}, review, "review-run"
    )
    assert category == "evidence"
    with pytest.raises(ValueError, match="reproduced bug"):
        apply_disposition(
            "evidence", {"decision": "not_reproduced"}, review, "review-run"
        )
    with pytest.raises(ValueError, match="existing contribution"):
        apply_disposition(
            "evidence", {}, {**review, "decision": "review_existing"}, "review-run"
        )


def test_closure_recommendation_stays_on_the_review_without_github_reads():
    recommendation = {"action": "close_duplicate", "target": {"number": 2}}
    review = {
        "decision": "propose_closure",
        "reason": "Duplicate of #2",
        "next_action": "Close as a duplicate of #2",
        "findings": [],
        "recommendations": [recommendation],
    }
    category, result = apply_disposition(
        "decisions", {"issue_number": 1}, review, "review-run"
    )
    assert category == "pruning"
    assert result["adversarial_review"]["recommendations"] == [recommendation]
    assert "mutation_recommendations" not in result


@pytest.mark.parametrize(
    "destination", ["evidence", "existing_review", "pruning", "decisions", "unchanged"]
)
def test_every_public_facing_outcome_requires_adversarial_review(destination):
    assert requires_review(destination)


def test_operational_failures_are_not_triage_decisions():
    assert not requires_review("operational")


def test_native_triage_cannot_run_without_adversarial_review():
    plans = {
        role: AgentPlan(flow_id=uuid4(), version_id=uuid4())
        for role in ("assess", "reproduce")
    }
    native = {
        "source_sha": "b" * 40,
        "triage_plan": AgentPlan(flow_id=uuid4(), version_id=uuid4()),
        "triage_verify_id": uuid4(),
        "triage_finalize_id": uuid4(),
    }
    with pytest.raises(ValueError, match="adversarial triage review"):
        Runtime(plans=plans, **native)
    plans["triage_review"] = AgentPlan(flow_id=uuid4(), version_id=uuid4())
    Runtime(plans=plans, **native)


def test_public_facing_reviewers_run_on_sol():
    directory = Path(__file__).resolve().parents[1] / "workflows"
    plans = build_plans(directory)
    for role in ("triage_review",):
        assert plans[role]["nodes"][role]["model"]["id"] == "gpt-6.1-sol"
        assert (
            json.loads((directory / f"fastmcp-stack-{role}.plan.json").read_text())
            == plans[role]
        )


def test_adversarial_standards_live_in_an_editable_repository_skill():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "workflows/stack-skills.json").read_text())
    skill = ".agents/skills/adversarial-review/SKILL.md"
    assert skill in manifest["roles"]["triage_review"]
    assert (
        (root.parents[1] / skill)
        .read_text()
        .startswith("---\nname: adversarial-review\n")
    )
    objective = build_plans(root / "workflows")["triage_review"]["nodes"][
        "triage_review"
    ]["objective"]
    assert "adversarial-review skill in instructions.files" in objective
