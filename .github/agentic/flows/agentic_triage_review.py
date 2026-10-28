from __future__ import annotations


def apply_disposition(
    destination: str, outcome: dict, review: dict, run_id: str
) -> tuple[str, dict]:
    decision = review["decision"]
    if decision == "prepare_fix" and outcome.get("decision") != "reproduced":
        raise ValueError("A fix recommendation requires a reproduced bug")
    if decision == "review_existing" and destination != "existing_review":
        raise ValueError(
            "A contribution recommendation requires existing contribution evidence"
        )
    destinations = {
        "prepare_fix": "evidence",
        "review_existing": "existing_review",
        "repair_evidence": "evidence",
        "maintainer": "decisions",
        "skip": "unchanged",
        "environment_failure": "operational",
    }
    result = {
        **outcome,
        "adversarial_review": review,
        "triage_review_run_id": run_id,
        "next_step": review["next_action"],
    }
    destination = destinations[decision]
    if decision == "repair_evidence":
        result["decision"] = "evidence_needed"
    if (
        decision == "environment_failure"
        or decision == "prepare_fix"
        and review["findings"]
    ):
        destination = (
            "operational" if decision == "environment_failure" else "decisions"
        )
        result["reason"] = review["reason"]
    return destination, result
