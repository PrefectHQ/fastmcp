from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from uuid import UUID

from flows.agentic_existing_review import verify_existing
from flows.agentic_freshness import change_kind
from flows.agentic_intake import route_intake
from flows.agentic_stack import digest
from flows.agentic_stack_context import comparison, fields
from flows.agentic_stack_policy import Batch, Group
from flows.agentic_stack_publication import GitHub
from flows.agentic_stack_runtime import Runtime, record, saved, secret_sync, skills


def refresh_context(item: dict, current: dict, read: Callable[[str], dict]) -> dict:
    original = item["evidence"]
    if change_kind(original, current) != "baseline_changed":
        raise ValueError("Baseline review requires unchanged contribution evidence")
    route = route_intake(current)
    if {k: v for k, v in route.items() if k != "revision"} != {
        k: v for k, v in item["route"].items() if k != "revision"
    }:
        raise ValueError("Contribution routing changed")
    delta = comparison(read, original["base_sha"], current["base_sha"], 48000)
    if (
        delta["status"] != "ahead"
        or delta.get("behind_by") != 0
        or not delta["complete"]
        or len(json.dumps(delta).encode()) > 64000
    ):
        raise ValueError(
            "Baseline review requires a complete bounded fast-forward diff"
        )
    evidence = fields(
        current, "schema_version complete base_sha issue reactions stacks"
    )
    evidence["prs"] = [
        fields(
            pr,
            "number title body state draft merged html_url labels base_ref base_sha head_sha head_repository user",
        )
        for pr in current["prs"]
    ]
    return {
        "evidence": evidence,
        "route": route,
        "group_rationale": item["group_rationale"],
        "previous_assessment": item["assessment"],
        "baseline_change": delta,
        "previous_evidence_sha256": digest(original),
        "retained_intake_sha256": digest(current),
        "context_policy": (
            "This is a baseline refresh of a completed contribution assessment. "
            "The complete issue discussion, PR heads, reviews, holds and human interventions "
            "are unchanged and retained in the previous intake. This context omits their repeated text. "
            "The previous assessment is historical judgment, not approval of the new baseline. "
            "Assess the supplied intervening commits against its contract and findings; inspect "
            "source/history when needed. State whether the existing contribution remains justified. "
            "Deterministic tests on the new baseline follow separately; do not infer their outcome."
        ),
    }


def review_existing(
    owner: UUID,
    batch: Batch,
    group: Group,
    runtime: Runtime,
    original: dict,
    probes: dict[str, str],
    instructions: dict,
    ask: Callable[[str, dict, dict, str], tuple[dict, str]],
    load_skills: Callable[[str], dict] = skills,
    read_compare: Callable[[str], dict] | None = None,
) -> tuple[str, dict]:
    item = deepcopy(original)
    number = item["evidence"]["issue"]["number"]
    for attempt in range(3):
        stage = "initial" if attempt == 0 else f"refresh-{attempt}"
        target = group.model_copy(update={"base_sha": item["evidence"]["base_sha"]})
        outcome = verify_existing(owner, batch, target, runtime, item, probes, stage)
        review, current = outcome["review"], outcome["observed"]
        if any(c["status"] == "environment_failure" for c in review["verification"]):
            review["status"] = "environment_failure"
            review["reason"] = (
                "Contribution verification could not execute successfully"
            )
            return "operational", review
        if review["status"] != "baseline_changed" or attempt == 2:
            record(
                owner, f"{batch.id}-issue-{number}-review-evidence", item["evidence"]
            )
            return "existing_review", review
        key = f"{batch.id}-issue-{number}-baseline-{attempt + 1}"
        refresh = saved(owner, key)
        if refresh is None:
            new_instructions = load_skills(current["base_sha"])["existing_instructions"]
            if {k: v for k, v in new_instructions.items() if k != "base_sha"} != {
                k: v for k, v in instructions.items() if k != "base_sha"
            }:
                review["status"] = "stale"
                review["next_step"] = (
                    "Contribution policy changed; reassess with current skills"
                )
                return "existing_review", review
            if read_compare is None:
                github = GitHub(secret_sync("fastmcp-triage-gh-token"))

                def read(endpoint: str) -> dict:
                    value = github.request(endpoint)
                    if not isinstance(value, dict):
                        raise ValueError("Expected a GitHub comparison object")
                    return value
            else:
                read = read_compare

            try:
                context = refresh_context(item, current, read)
            except ValueError:
                review["next_step"] = (
                    "Baseline diff needs a full assessment on the next intake"
                )
                return "existing_review", review
            refresh = {
                "context": context,
                "evidence": current,
                "instructions": new_instructions,
            }
            record(owner, key, refresh)
        assessment, run_id = ask(
            "assess",
            refresh["instructions"],
            refresh["context"],
            f"issue-{number}-baseline-{attempt + 1}-assess",
        )
        item = {
            "evidence": refresh["evidence"],
            "route": refresh["context"]["route"],
            "assessment": assessment,
            "group_rationale": original["group_rationale"],
        }
        instructions = refresh["instructions"]
        if assessment["decision"] != "existing_review":
            record(
                owner, f"{batch.id}-issue-{number}-review-evidence", item["evidence"]
            )
            destination = {
                "environment_failure": "operational",
                "prune": "pruning",
                "skip": "unchanged",
                "evidence_needed": "evidence",
            }.get(assessment["decision"], "decisions")
            return destination, {
                "issue_number": number,
                **assessment,
                "assessment_run_id": run_id,
            }
    raise AssertionError("Bounded baseline review did not return")
