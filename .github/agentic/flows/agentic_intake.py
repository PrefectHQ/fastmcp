from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime

POLICY_VERSION = "fastmcp-stacks-2"
AREA_LABELS = {
    "auth",
    "client",
    "server",
    "http",
    "openapi",
    "cli",
    "provider",
    "tasks",
}
CONTROL_LABELS = {
    "missing-issue-link",
    "bypass-issue-check",
    "trusted-contributor",
    "prs welcome",
    "triage-martian",
}
HOLD = re.compile(
    r"\b(?:dnm|do[\s_-]*not[\s_-]*merge|don['’]?t[\s_-]*merge|draft)\b", re.I
)


def revision_evidence(evidence: dict) -> dict:
    content = deepcopy(evidence)
    content.pop("collected_at", None)
    timelines = [content.get("timeline", [])]
    timelines.extend(pr.get("timeline", []) for pr in content.get("prs", []))
    for timeline in timelines:
        for event in timeline:
            source = (event.get("source") or {}).get("issue") or {}
            repository = source.get("repository") or {}
            for field in (
                "forks_count",
                "forks",
                "watchers_count",
                "watchers",
                "stargazers_count",
                "open_issues_count",
                "open_issues",
                "size",
                "updated_at",
                "pushed_at",
            ):
                repository.pop(field, None)
    return content


def evidence_revision(evidence: dict) -> str:
    content = revision_evidence(evidence)
    payload = json.dumps(
        [POLICY_VERSION, content], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def directly_addresses(body: str, issue_number: int) -> bool:
    reference = (
        rf"(?:https://(?:redirect\.)?github\.com/PrefectHQ/fastmcp/issues/"
        rf"|PrefectHQ/fastmcp\#|\#){issue_number}(?![\w/])"
    )
    return (
        re.search(
            rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*{reference}",
            body,
            re.I,
        )
        is not None
    )


def held(pr: dict) -> bool:
    return bool(
        pr.get("draft")
        or HOLD.search(
            "\n".join(
                [pr.get("title", ""), pr.get("body") or "", *pr.get("labels", [])]
            )
        )
    )


def after(value: str | None, cutoff: str) -> bool:
    return bool(
        value
        and datetime.fromisoformat(value.replace("Z", "+00:00"))
        > datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    )


def duplicate_intervention(evidence: dict) -> bool:
    comments = evidence["comments"]
    markings = [
        comment
        for comment in comments
        if comment["user"]["type"] == "Bot"
        and "possible duplicate issues" in comment["body"].lower()
    ]
    if any(
        comment["user"]["type"] == "Bot"
        and "closing this issue as a duplicate" in comment["body"].lower()
        for comment in comments
    ):
        return True
    if not markings:
        return False
    cutoff = min(comment["created_at"] for comment in markings)
    marked_ids = {comment["id"] for comment in markings}
    return (
        any(
            comment["user"]["type"] != "Bot" and after(comment["created_at"], cutoff)
            for comment in comments
        )
        or any(
            reaction["comment_id"] in marked_ids
            and reaction["content"] in {"-1", "confused"}
            for reaction in evidence["reactions"]
        )
        or any(
            event["event"] == "reopened" and after(event.get("created_at"), cutoff)
            for event in evidence["timeline"]
        )
    )


def route_intake(evidence: dict) -> dict:
    if evidence.get("complete") is not True or evidence.get("schema_version") != 2:
        return {
            "route": "retry",
            "reason": "Incomplete intake; no issue verdict",
            "policy_version": POLICY_VERSION,
        }
    issue = evidence["issue"]
    labels = set(issue["labels"])
    decision = {
        "issue_number": issue["number"],
        "revision": evidence_revision(evidence),
        "policy_version": POLICY_VERSION,
        "areas": sorted(labels & AREA_LABELS),
        "control_labels": sorted(labels & CONTROL_LABELS),
        "label_writes": [],
    }

    def result(route: str, reason: str, **details: object) -> dict:
        return {**decision, "route": route, "reason": reason, **details}

    if "security" in labels:
        return result(
            "restricted_review", "Security-labeled report requires restricted review"
        )
    if issue["state"] != "open":
        return result(
            "closed", "Already closed; retain history without launching a fixer"
        )
    direct = [
        pr
        for pr in evidence["prs"]
        if directly_addresses(pr.get("body") or "", issue["number"])
    ]
    if direct:
        return result(
            "review_existing",
            "Explicit fixing references; inspect contribution history before new work",
            pull_requests=[
                {
                    "number": pr["number"],
                    "held": held(pr),
                    "merged": pr["merged"],
                    "state": pr["state"],
                }
                for pr in direct
            ],
        )
    if issue["assignees"]:
        return result(
            "claimed",
            "Assigned work requires coordination; do not launch a competing fixer",
        )
    if any(
        event["event"] == "reopened" for event in evidence["timeline"]
    ) or duplicate_intervention(evidence):
        return result(
            "triage",
            "Preserve human intervention; do not repeat an automatic closure verdict",
            human_intervention=True,
        )
    if "potential-duplicate" in labels:
        return result(
            "duplicate_review",
            "Existing duplicate workflow owns its cooldown and closure; verify the proposed match",
        )
    if "needs MRE" in labels:
        applications = [
            event
            for event in evidence["timeline"]
            if event["event"] == "labeled"
            and (event.get("label") or {}).get("name") == "needs MRE"
        ]
        if not applications:
            return result(
                "triage",
                "Missing label application history; do not infer an inactivity deadline",
            )
        cutoff = max(event["created_at"] for event in applications)
        author = issue["user"]["login"]
        responded = any(
            comment["user"]["login"] == author and after(comment["created_at"], cutoff)
            for comment in evidence["comments"]
        )
        edited = any(
            event["event"] == "edited"
            and (event.get("actor") or {}).get("login") == author
            and after(event.get("created_at"), cutoff)
            for event in evidence["timeline"]
        )
        if responded or edited or after(issue["updated_at"], cutoff):
            return result(
                "triage",
                "Report has changed since the MRE request; inspect the new evidence",
            )
        return result(
            "evidence_needed",
            "Existing MRE request is outstanding; reuse it without another public comment",
            requested_at=cutoff,
        )
    if labels & {"invalid", "wontfix"}:
        return result(
            "pruning_review",
            "Existing disposition label is evidence for a recommendation, not permission to close",
        )
    if evidence["prs"]:
        return result("match", "Candidate contributions need bounded semantic matching")
    return result(
        "triage",
        "No direct contribution match; establish impact, intended contract and supported versions",
    )


def policy_outcome(issue_number: int | None, route: dict) -> dict:
    return {"issue_number": issue_number, "reason": route["reason"], "route": route}


def upgrade_policy_outcomes(result: dict) -> dict:
    # Controllers before policy_outcome binned the bare decision, whose
    # "route" is the route name rather than the decision.
    return {
        **result,
        "bins": {
            name: [
                policy_outcome(item.get("issue_number"), item)
                if isinstance(item.get("route"), str)
                else item
                for item in items
            ]
            for name, items in result["bins"].items()
        },
    }
