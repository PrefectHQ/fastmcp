from __future__ import annotations


def requires_review(destination: str) -> bool:
    return destination != "operational"


def apply_disposition(
    destination: str,
    outcome: dict,
    review: dict,
    run_id: str,
) -> tuple[str, dict]:
    decision = review["decision"]
    if decision == "prepare_fix" and outcome.get("decision") != "reproduced":
        raise ValueError("A fix recommendation requires a reproduced bug")
    if decision == "review_existing" and destination != "existing_review":
        raise ValueError(
            "A contribution recommendation requires existing contribution evidence"
        )
    if decision == "propose_closure" and (
        destination not in {"pruning", "existing_review", "decisions"}
        or not any(
            item["action"] in {"close_duplicate", "close_completed", "close_settled"}
            for item in review.get("recommendations", [])
        )
    ):
        raise ValueError(
            "Closure disposition requires a concrete retained closure candidate"
        )
    destinations = {
        "prepare_fix": "evidence",
        "review_existing": "existing_review",
        "propose_closure": "pruning",
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
    if decision == "environment_failure":
        destination = "operational"
        result["reason"] = review["reason"]
    return destination, result
