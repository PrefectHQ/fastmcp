import pytest

from flows.agentic_triage_review import apply_disposition


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


def test_findings_block_fix_preparation_and_contributions_require_evidence():
    review = {
        "decision": "prepare_fix",
        "reason": "Neighbor regression",
        "next_action": "Repair control case",
        "findings": ["Disabled tool must still resolve generated fallback"],
    }
    category, _ = apply_disposition(
        "evidence", {"decision": "reproduced"}, review, "review-run"
    )
    assert category == "decisions"
    with pytest.raises(ValueError, match="reproduced bug"):
        apply_disposition(
            "evidence", {"decision": "not_reproduced"}, review, "review-run"
        )
    with pytest.raises(ValueError, match="existing contribution"):
        apply_disposition(
            "evidence", {}, {**review, "decision": "review_existing"}, "review-run"
        )
