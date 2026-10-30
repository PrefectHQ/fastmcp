from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy

from flows.agentic_intake import directly_addresses, duplicate_intervention, held
from flows.agentic_mutation_models import MutationRecommendation
from flows.agentic_stack import digest
from flows.agentic_stack_policy import publication_revision

REPOSITORY = "PrefectHQ/fastmcp"
REQUIRED = {
    "assign_issue": {
        "scope_contract",
        "prior_discussion",
        "public_history",
        "reporter_first_claim",
        "bot_feedback",
    },
    "finish_pr": {"scope_contract", "prior_discussion", "bot_feedback"},
    "review_pr": {"scope_contract", "prior_discussion", "bot_feedback"},
    "close_duplicate": {"scope_contract", "prior_discussion", "canonical_equivalence"},
    "close_completed": {
        "scope_contract",
        "prior_discussion",
        "current_fix_and_reverts",
    },
    "close_settled": {"scope_contract", "prior_discussion", "settled_policy"},
    "close_pr": {
        "scope_contract",
        "prior_discussion",
        "reporter_first_claim",
        "bot_feedback",
        "maintainer_direction",
        "coverage_parity",
    },
}


def resolution_snapshot(value: dict) -> dict:
    snapshot = {
        name: value[name]
        for name in (
            "number",
            "title",
            "body",
            "state",
            "updated_at",
            "merged",
            "merged_at",
            "merge_commit_sha",
        )
        if name in value
    }
    for name in ("head", "base"):
        if isinstance(value.get(name), dict):
            side = value[name]
            snapshot[name] = {
                "sha": side.get("sha"),
                "ref": side.get("ref"),
                "repository": (side.get("repo") or {}).get("full_name"),
            }
    snapshot["labels"] = sorted(
        label.get("name") if isinstance(label, dict) else label
        for label in value.get("labels", [])
    )
    snapshot["assignees"] = sorted(
        person.get("login") if isinstance(person, dict) else person
        for person in value.get("assignees", [])
    )
    return snapshot


def qualify_recommendations(
    outcome: dict,
    review: dict,
    run_id: str,
    evidence: dict | None,
    read: Callable[[str], dict | None] | None = None,
) -> list[dict]:
    evidence = evidence or {}
    issue = evidence.get("issue", {})
    prs = {pr["number"]: pr for pr in evidence.get("prs", [])}
    intervention = any(
        event.get("event") == "reopened" for event in evidence.get("timeline", [])
    ) or duplicate_intervention(
        {
            "comments": evidence.get("comments", []),
            "timeline": evidence.get("timeline", []),
            "reactions": evidence.get("reactions", []),
        }
    )
    route = outcome.get("route")
    if not isinstance(route, dict):
        route = {}
    resolved = []
    reference_cache: dict[str, dict] = {}
    for raw in review.get("recommendations", []):
        proposal = MutationRecommendation.model_validate(raw).model_dump(mode="json")
        action, number = proposal["action"], proposal["target_number"]
        is_pr = action in {"finish_pr", "review_pr", "close_pr"}
        pr = prs.get(number if is_pr else proposal.get("related_pr"))
        checks = {
            item["check"] for item in proposal["norm_evidence"] if item["satisfied"]
        }
        gates = {
            check: "passed" if check in checks else "required"
            for check in sorted(REQUIRED[action])
        }
        gates.update(
            independent_review="passed"
            if run_id and review.get("decision") != "environment_failure"
            else "required",
            complete_intake="passed"
            if evidence.get("complete") is True and evidence.get("schema_version") == 2
            else "required",
            target_identity="passed"
            if (pr if is_pr else number == issue.get("number"))
            else "required",
            evidence_binding="passed"
            if proposal["evidence_sha256"] == digest(evidence)
            else "required",
            scope_notes="passed" if proposal["scope_notes"].strip() else "required",
            fresh_evidence="required"
            if outcome.get("status")
            in {"stale", "baseline_changed", "environment_failure", "refresh_failed"}
            else "passed",
        )
        gates["action_disposition"] = (
            "passed"
            if review.get("decision")
            == (
                "propose_closure"
                if action in {"close_duplicate", "close_completed", "close_settled"}
                else "review_existing"
            )
            else "required"
        )
        if issue.get("state") != "open" or "security" in issue.get("labels", []):
            gates["open_unrestricted_issue"] = "required"
        if review.get("findings") and action in {
            "close_duplicate",
            "close_completed",
            "close_settled",
        }:
            gates["unresolved_findings"] = "required"
        gates["baseline_identity"] = (
            "passed"
            if re.fullmatch(r"[a-f0-9]{40}", evidence.get("base_sha") or "")
            else "required"
        )
        references = []
        if is_pr or action == "assign_issue":
            gates["target_revision"] = (
                "passed"
                if pr
                and proposal["head_sha"]
                and proposal["base_sha"]
                and proposal["head_sha"] == pr["head_sha"]
                and proposal["base_sha"] == pr["base_sha"]
                else "required"
            )
            gates["contribution_hold"] = (
                "required" if pr is None or held(pr) else "passed"
            )
            if action == "assign_issue":
                gates["platform_assignment_eligibility"] = "required"
                author = (pr or {}).get("user", {}).get("login")
                verified = next(
                    (
                        check
                        for check in outcome.get("verification", [])
                        if check.get("pr_number") == proposal["related_pr"]
                        and check.get("head_sha") == proposal["head_sha"]
                        and check.get("status") == "verification_recorded"
                    ),
                    {},
                )
                gates["executed_verification"] = (
                    "passed"
                    if verified.get("tests", {}).get("head", {}).get("status")
                    == "passed"
                    and not verified.get("comparison", {}).get("regressed_tests")
                    else "required"
                )
                gates["assignee_identity"] = (
                    "passed"
                    if author and proposal["assignee"] == author
                    else "required"
                )
                gates["existing_ownership"] = (
                    "passed"
                    if not issue.get("assignees") or issue["assignees"] == [author]
                    else "required"
                )
                gates["linked_unmerged_contribution"] = (
                    "passed"
                    if pr
                    and not pr.get("merged")
                    and directly_addresses(pr.get("body") or "", number)
                    else "required"
                )
                if issue.get("assignees") == [author]:
                    gates["assignment_needed"] = "required"
                if (
                    pr
                    and pr.get("state") != "open"
                    and (
                        not any(
                            "<!-- require-issue-link -->" in c.get("body", "")
                            and c.get("user", {}).get("type") == "Bot"
                            for c in pr.get("comments", [])
                        )
                        or next(
                            (
                                (event.get("actor") or {}).get("type")
                                for event in reversed(pr.get("timeline", []))
                                if event.get("event") == "closed"
                            ),
                            None,
                        )
                        != "Bot"
                    )
                ):
                    gates["assignment_gate_history"] = "required"
            else:
                gates["open_contribution"] = (
                    "passed"
                    if pr and pr.get("state") == "open" and not pr.get("merged")
                    else "required"
                )
        if action.startswith("close_"):
            gates["public_closure_reason"] = (
                "passed" if proposal["public_comment"].strip() else "required"
            )
            if not is_pr:
                gates["existing_ownership"] = (
                    "required" if issue.get("assignees") else "passed"
                )
            if not is_pr and any(
                pr.get("state") == "open" and not pr.get("merged")
                for pr in prs.values()
            ):
                gates["active_contribution_coordination"] = "required"
        if action in {"close_duplicate", "close_completed", "close_pr"}:
            related = proposal[
                "canonical_issue"
                if action == "close_duplicate"
                else "canonical_pr"
                if action == "close_pr"
                else "merged_pr"
            ]
            path = "issues" if action == "close_duplicate" else "pulls"
            related_value: dict = {}
            if (
                related
                and related != number
                and read
                and gates["evidence_binding"] == "passed"
            ):
                endpoint = f"repos/{REPOSITORY}/{path}/{related}"
                if endpoint not in reference_cache:
                    try:
                        reference_cache[endpoint] = read(endpoint) or {}
                    except (ValueError, OSError):
                        reference_cache[endpoint] = {}
                related_value = reference_cache[endpoint]
            url = f"https://github.com/{REPOSITORY}/{'pull' if path == 'pulls' else path}/{related}"
            valid = (
                isinstance(related_value, dict)
                and related_value.get("number") == related
                and related_value.get("html_url") == url
            )
            if action == "close_duplicate":
                valid = valid and "pull_request" not in related_value
                gates["canonical_identity"] = "passed" if valid else "required"
                if (
                    "potential-duplicate" in issue.get("labels", [])
                    or intervention
                    or route.get("human_intervention")
                ):
                    gates["existing_duplicate_automation_or_intervention"] = "required"
            elif action == "close_pr":
                gates["canonical_identity"] = "passed" if valid else "required"
                gates["canonical_reopen_coordination"] = (
                    "passed"
                    if related_value.get("state") == "open"
                    or related_value.get("merged") is True
                    else "required"
                )
                canonical_head = (related_value.get("head") or {}).get("sha")
                canonical_base = (related_value.get("base") or {}).get("sha")
                gates["canonical_revision"] = (
                    "passed"
                    if valid
                    and proposal["canonical_head_sha"]
                    and proposal["canonical_head_sha"] == canonical_head
                    and (
                        not proposal["canonical_base_sha"]
                        or proposal["canonical_base_sha"] == canonical_base
                    )
                    else "required"
                )
                verified = {
                    check["pr_number"]: check
                    for check in outcome.get("verification", [])
                    if check.get("status") == "verification_recorded"
                }
                target_check, canonical_check = (
                    verified.get(number, {}),
                    verified.get(related, {}),
                )
                gates["executed_comparison"] = (
                    "passed"
                    if target_check.get("head_sha") == proposal["head_sha"]
                    and canonical_check.get("head_sha")
                    == proposal["canonical_head_sha"]
                    else "required"
                )
                if (
                    canonical_check.get("comparison", {}).get("regressed_tests")
                    or canonical_check.get("tests", {}).get("head", {}).get("status")
                    != "passed"
                ):
                    gates["canonical_behavior"] = "required"
                if (
                    related_value.get("merged")
                    and "current_fix_and_reverts" not in checks
                ):
                    gates["current_fix_and_reverts"] = "required"
            else:
                valid = (
                    valid
                    and related_value.get("merged") is True
                    and directly_addresses(related_value.get("body") or "", number)
                )
                gates["merged_complete_fix"] = "passed" if valid else "required"
            if valid:
                if "security" in [
                    label.get("name") if isinstance(label, dict) else label
                    for label in related_value.get("labels", [])
                ]:
                    gates["restricted_resolution_evidence"] = "required"
                gates["closure_reference_in_comment"] = (
                    "passed"
                    if url in proposal["public_comment"]
                    or re.search(
                        rf"(?<![\w/])#{related}(?!\d)", proposal["public_comment"]
                    )
                    else "required"
                )
                references.append(
                    {
                        "url": url,
                        "sha256": digest(resolution_snapshot(related_value)),
                        "updated_at": related_value.get("updated_at"),
                    }
                )
        if action == "close_settled":
            policy = proposal["policy_url"] or ""
            gates["settled_policy_reference"] = (
                "passed"
                if policy.startswith(
                    (f"https://github.com/{REPOSITORY}/", "https://gofastmcp.com/")
                )
                else "required"
            )
            if policy and policy not in proposal["public_comment"]:
                gates["closure_reference_in_comment"] = "required"
        for name in (
            "static_checks",
            "current_ci",
            "batch_composition",
            "contributor_credit",
        ):
            gates[name] = (
                "required_before_merge"
                if action in {"finish_pr", "review_pr"}
                else "not_applicable"
            )
        required = sorted(
            name for name, status in gates.items() if status == "required"
        )
        resolved.append(
            {
                "id": digest([proposal, run_id, digest(evidence), references])[:24],
                "action": action,
                "target": {
                    "kind": "pull_request" if is_pr else "issue",
                    "number": number,
                    "url": f"https://github.com/{REPOSITORY}/{'pull' if is_pr else 'issues'}/{number}",
                },
                "issue_number": issue.get("number", outcome.get("issue_number")),
                "payload": {
                    key: proposal[key]
                    for key in (
                        "assignee",
                        "related_pr",
                        "canonical_issue",
                        "merged_pr",
                        "canonical_pr",
                        "canonical_head_sha",
                        "canonical_base_sha",
                        "policy_url",
                        "public_comment",
                    )
                    if proposal[key]
                },
                "reason": proposal["reason"],
                "next_step": proposal["next_step"],
                "scope_notes": proposal["scope_notes"],
                "head_sha": proposal["head_sha"],
                "base_sha": proposal["base_sha"],
                "baseline_sha": evidence.get("base_sha"),
                "evidence_sha256": digest(evidence) if evidence else None,
                "evidence_kind": "retained_intake",
                "evidence_revision": publication_revision(evidence)
                if evidence.get("complete") and evidence.get("schema_version") == 2
                else None,
                "source_runs": {
                    "triage_review": run_id,
                    "verification": [
                        c["run_id"]
                        for c in outcome.get("verification", [])
                        if c.get("pr_number")
                        in {(pr or {}).get("number"), proposal.get("canonical_pr")}
                    ],
                },
                "norm_evidence": proposal["norm_evidence"],
                "references": references,
                "gates": gates,
                "remaining_gates": required,
                "before_merge": sorted(
                    name
                    for name, status in gates.items()
                    if status == "required_before_merge"
                ),
                "status": "blocked" if required else "proposed",
                "approval_required": True,
                "merge_ready": False,
                "execution_preconditions": [
                    "Refresh the exact issue, PR revisions, discussion and referenced resolution evidence",
                    "Acquire the existing issue/PR claim",
                    "Respect contributor ownership, security restrictions and explicit holds",
                    "Obtain maintainer approval for this exact mutation",
                    *(
                        [
                            f"Verify GET /repos/{REPOSITORY}/assignees/{proposal['assignee']} returns 204; read back issue assignees after assignment, since a successful CLI exit can silently ignore an ineligible user"
                        ]
                        if action == "assign_issue"
                        else []
                    ),
                ],
            }
        )
    return resolved


def retained_pr_steps(item: dict, category: str) -> list[dict]:
    if item.get("status") in {
        "stale",
        "baseline_changed",
        "environment_failure",
        "refresh_failed",
    }:
        return []
    checks: list[dict] = item.get("verification", [])
    generated = category == "stacks" and item.get("status") == "published"
    if generated:
        receipts = {
            receipt["pr_number"]: receipt
            for receipt in item.get("review_delivery", {}).get("posted", [])
        }
        checks = [
            {
                "pr_number": number,
                "head_sha": receipts.get(number, {}).get("head_sha"),
                "baseline_sha": receipts.get(number, {}).get("baseline_sha"),
                "run_id": item.get("validation_run_id"),
            }
            for number in item.get("pull_requests", [])
        ]
    result = []
    for check in checks:
        number, head, base = (
            check["pr_number"],
            check.get("head_sha"),
            check.get("baseline_sha"),
        )
        regressions = check.get("comparison", {}).get("regressed_tests", [])
        action = "finish_pr" if regressions else "review_pr"
        review = item.get("adversarial_review", {})
        step = (
            "Repair the reproduced regressions before further merge review: "
            + ", ".join(regressions)
            if regressions
            else item.get("next_step")
            or f"Review PR #{number} at the retained revisions against its linked issue and repository skills."
        )
        gates = {
            "target_revision": "passed" if head and base else "required",
            "independent_review": "passed"
            if item.get("triage_review_run_id") or item.get("review_run_id")
            else "required",
            "target_skill_review": "required",
            "static_checks": "required_before_merge",
            "current_ci": "required_before_merge",
            "batch_composition": "required_before_merge",
            "contributor_credit": "required_before_merge",
        }
        if check.get("status") in {"environment_failure", "evidence_needed"}:
            gates["executed_verification"] = "required"
        if any(
            pr.get("number") == number and pr.get("held")
            for pr in item.get("route", {}).get("pull_requests", [])
        ):
            gates["contribution_hold"] = "required"
        remaining = sorted(
            name for name, status in gates.items() if status == "required"
        )
        result.append(
            {
                "id": digest([action, number, head, base, digest(item)])[:24],
                "action": action,
                "target": {
                    "kind": "pull_request",
                    "number": number,
                    "url": f"https://github.com/{REPOSITORY}/pull/{number}",
                },
                "issue_number": item.get("issue_number"),
                "payload": {},
                "reason": review.get("reason")
                or item.get("assessment", {}).get("reason")
                or "Retained generated patch validation and independent review are available.",
                "next_step": step,
                "scope_notes": "Recorded verification is evidence for review; compatibility, contribution policy and merge readiness remain separate.",
                "head_sha": head,
                "base_sha": base,
                "baseline_sha": base,
                "evidence_sha256": digest(item),
                "evidence_kind": "retained_outcome",
                "evidence_revision": item.get("route", {}).get("revision"),
                "source_runs": {
                    "verification": [check["run_id"]] if check.get("run_id") else [],
                    "triage_review": item.get("triage_review_run_id"),
                    "patch_review": item.get("review_run_id"),
                    "validation": item.get("validation_run_id"),
                },
                "norm_evidence": [],
                "references": [],
                "gates": gates,
                "remaining_gates": remaining,
                "before_merge": sorted(
                    name
                    for name, status in gates.items()
                    if status == "required_before_merge"
                ),
                "status": "blocked" if remaining else "proposed",
                "approval_required": True,
                "merge_ready": False,
                "execution_preconditions": [
                    "Refresh and claim this target before acting",
                    "Respect ownership and explicit holds",
                    "Obtain approval for the exact action",
                ],
            }
        )
    return result


def mutation_batch(result: dict, owner: str | None = None) -> dict:
    recommendations = []
    for category, items in result.get("bins", {}).items():
        if category in {"operational", "unchanged"}:
            continue
        for item in items:
            candidates = item.get("mutation_recommendations")
            if not candidates:
                candidates = retained_pr_steps(item, category)
            for recommendation in candidates:
                value = deepcopy(recommendation)
                value["acknowledged_reviews"] = deepcopy(
                    item.get("published_reviews", [])
                    + item.get("review_delivery", {}).get("posted", [])
                )
                if item.get("status") in {
                    "stale",
                    "baseline_changed",
                    "environment_failure",
                    "refresh_failed",
                }:
                    value["gates"]["fresh_evidence"] = "required"
                    value["remaining_gates"] = sorted(
                        set(value["remaining_gates"]) | {"fresh_evidence"}
                    )
                    value["status"] = "blocked"
                recommendations.append(value)
    grouped = defaultdict(list)
    for item in recommendations:
        grouped[(item["target"]["kind"], item["target"]["number"])].append(item)
    for items in grouped.values():
        if (
            len(
                {
                    digest(
                        {
                            key: item.get(key)
                            for key in ("action", "payload", "head_sha", "base_sha")
                        }
                    )
                    for item in items
                }
            )
            > 1
        ):
            for item in items:
                item["gates"]["conflicting_recommendations"] = "required"
                item["remaining_gates"] = sorted(
                    set(item["remaining_gates"]) | {"conflicting_recommendations"}
                )
                item["status"] = "blocked"
    closing = {
        (item["target"]["kind"], item["target"]["number"]): item
        for item in recommendations
        if item["action"] in {"close_pr", "close_duplicate"}
    }
    for item in recommendations:
        dependency = None
        if item["action"] == "close_pr":
            dependency = ("pull_request", item["payload"].get("canonical_pr"))
        elif item["action"] == "close_duplicate":
            dependency = ("issue", item["payload"].get("canonical_issue"))
        elif item["action"] == "assign_issue":
            dependency = ("pull_request", item["payload"].get("related_pr"))
        if dependency in closing:
            for conflict in (item, closing[dependency]):
                conflict["gates"]["canonical_survival_or_assignment_conflict"] = (
                    "required"
                )
                conflict["remaining_gates"] = sorted(
                    set(conflict["remaining_gates"])
                    | {"canonical_survival_or_assignment_conflict"}
                )
                conflict["status"] = "blocked"
    unique = {digest(item): item for item in recommendations}
    values = sorted(
        unique.values(),
        key=lambda item: (
            item["target"]["kind"],
            item["target"]["number"],
            item["action"],
        ),
    )
    return {
        "schema_version": "fastmcp-mutation-batch/1",
        "batch_id": result["batch_id"],
        "source_run_id": owner,
        "approval_required": True,
        "execution_enabled": False,
        "recommendations": values,
        "counts": dict(Counter(item["status"] for item in values)),
    }
