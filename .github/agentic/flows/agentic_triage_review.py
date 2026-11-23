from __future__ import annotations


def requires_review(destination: str) -> bool:
    return destination != "operational"


def apply_disposition(
    destination: str,
    outcome: dict,
    review: dict,
    run_id: str,
    route: dict,
) -> tuple[str, dict]:
    decision = review["decision"]
    if decision == "prepare_fix" and outcome.get("decision") != "reproduced":
        # The reviewer wants a fix the reproduction didn't confirm. Don't prepare
        # one; put the disagreement in front of a maintainer instead of dropping it.
        reproduction = outcome.get("decision") or "not attempted"
        return "decisions", {
            **outcome,
            "adversarial_review": review,
            "triage_review_run_id": run_id,
            "next_step": review["next_action"],
            "question": f"Reviewer recommends a fix, but reproduction was {reproduction}: "
            + review["next_action"],
        }
    # Intake routes an issue to review_existing when a PR claims to fix it, even
    # when native triage then files it under decisions; either is evidence. A
    # reviewer who finds a contribution intake didn't (a PR that only mentions
    # the issue, say) hands it to a maintainer rather than failing the item.
    unadmitted_review = (
        decision == "review_existing"
        and destination != "existing_review"
        and route.get("route") != "review_existing"
    )
    unsupported_closure = decision == "propose_closure" and (
        destination not in {"pruning", "existing_review", "decisions"}
        or not any(
            item["action"] in {"close_duplicate", "close_completed", "close_settled"}
            for item in review.get("recommendations", [])
        )
    )
    if unadmitted_review or unsupported_closure:
        ask = (
            "Reviewer points to a contribution triage didn't admit"
            if unadmitted_review
            else "Reviewer proposes closure without a retained closure candidate"
        )
        return "decisions", {
            **outcome,
            "adversarial_review": review,
            "triage_review_run_id": run_id,
            "next_step": review["next_action"],
            "question": f"{ask}: " + review["next_action"],
        }
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
