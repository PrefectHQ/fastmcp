from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from copy import deepcopy

from flows.agentic_github import REPOSITORY, GitHub
from flows.agentic_intake import HOLD
from flows.agentic_review_feedback import (
    MARKER,
    attach_feedback,
    normalized,
    read_feedback,
)
from flows.agentic_stack_plans import PublicReview


class EvidenceChanged(ValueError):
    """The issue or PR moved after review; the new revision is triaged on its own."""

    def __init__(self, reason: str, posted: list[dict] | None = None):
        super().__init__(reason)
        self.posted = posted or []


def before_delivery(evidence: dict, receipts: list[dict], reviewer: str) -> dict:
    result = deepcopy(evidence)
    for receipt in receipts:
        for pr in result["prs"]:
            if pr["number"] != receipt["pr_number"]:
                continue
            pr.pop("updated_at", None)
            own = {
                review["id"]
                for review in pr.get("reviews", [])
                if review["id"] == receipt["review_id"]
                and review.get("user", {}).get("login") == reviewer
                and review.get("user", {}).get("type") == "Bot"
                and review.get("state") == "COMMENTED"
                and review.get("commit_id") == receipt.get("head_sha")
                and hashlib.sha256((review.get("body") or "").encode()).hexdigest()
                == receipt.get("body_sha256")
            }
            pr["reviews"] = [
                review for review in pr.get("reviews", []) if review["id"] not in own
            ]
            pr["timeline"] = [
                event
                for event in pr.get("timeline", [])
                if not (event.get("id") in own and event.get("event") == "reviewed")
            ]
    return result


def format_review(
    number: int, head: str, baseline: str, summary: str, details: str
) -> str:
    details = re.sub(
        r"</?(?:details|summary)\b[^>]*>",
        lambda match: html.escape(match.group()),
        details,
        flags=re.IGNORECASE,
    )
    return "\n\n".join(
        [
            f"<!-- fastmcp-triage-review:v1 pr:{number} head:{head} base:{baseline} -->",
            "Automated review by the **FastMCP triage reviewer bot**.",
            summary,
            "<details>\n<summary>Checks</summary>",
            details,
            "</details>",
        ]
    )


def review_body(number: int, head: str, baseline: str, result: dict) -> str:
    review = result["adversarial_review"]
    if review["decision"] != "review_existing":
        raise ValueError("Publication requires an independent contribution review")
    candidates = [
        item
        for item in review.get("public_reviews", [])
        if item.get("pr_number") == number
    ]
    if len(candidates) != 1:
        raise ValueError("Review needs one retained public-feedback entry for this PR")
    public = PublicReview.model_validate(candidates[0])
    summary = public.summary
    if public.requested_change:
        summary += "\n\n" + public.requested_change
    body = format_review(number, head, baseline, summary, public.evidence)
    return attach_feedback(body, public)


def post_review(
    github: GitHub,
    number: int,
    head: str,
    baseline: str,
    reviewer: str,
    body: str,
    guard: Callable[[], None],
    *,
    allow_draft: bool = False,
) -> dict:
    if number < 1 or not all(
        re.fullmatch(r"[a-f0-9]{40}", x) for x in (head, baseline)
    ):
        raise ValueError("Review requires a PR and immutable revisions")
    prefix = f"repos/{REPOSITORY}/pulls/{number}"
    marker = (
        f"<!-- fastmcp-triage-review:v1 pr:{number} head:{head} base:{baseline} -->"
    )
    if not body.startswith(marker) or len(body.encode()) > 50000:
        raise ValueError("Review body is unbound or too large")

    def current() -> dict:
        pr = github.request(prefix)
        if not isinstance(pr, dict) or pr.get("head", {}).get("sha") != head:
            raise EvidenceChanged("PR head changed after review")
        text = "\n".join(
            [pr["title"], pr.get("body") or "", *[x["name"] for x in pr["labels"]]]
        )
        if (
            pr["state"] != "open"
            or pr.get("merged")
            or (pr["draft"] and not allow_draft)
            or HOLD.search(text)
        ):
            raise EvidenceChanged("PR is closed, draft or explicitly held")
        if pr["user"]["login"] == reviewer:
            raise ValueError("Reviewer must differ from PR author")
        return pr

    current()
    # The caller holds the existing per-PR claim across this read and decision.
    reviews = [
        value
        for value in github.pages(prefix + "/reviews")
        if value.get("user", {}).get("login") == reviewer
        and value.get("user", {}).get("type") == "Bot"
    ]
    latest = max(reviews, key=lambda value: value["id"], default=None)
    existing = bool(
        latest
        and latest.get("state") == "COMMENTED"
        and latest.get("commit_id") == head
        and normalized(latest.get("body") or "") == normalized(body)
    )
    if existing:
        # Reconcile an accepted POST whose response was lost, without posting again.
        review = latest
    else:
        feedback = read_feedback(body, number, head)
        previous = (
            read_feedback(
                latest.get("body") or "", number, latest.get("commit_id") or ""
            )
            if latest and latest.get("state") == "COMMENTED"
            else None
        )
        guard()
        current()
        if (
            latest is not None
            and feedback is not None
            and feedback["outcome"] != "inconclusive"
            and feedback == previous
        ):
            return {
                "pr_number": number,
                "status": "unchanged_feedback",
                "verified_head_sha": head,
                "verified_baseline_sha": baseline,
                "reason": "Fresh review and verification found unchanged public feedback",
                "previous_review": review_receipt(number, latest, reviewer),
            }
        # Legacy/edited same-revision prose must not be silently adopted. Structured
        # changed findings, including resolutions, can produce a new guarded review.
        if (
            latest
            and (latest.get("body") or "").startswith(marker)
            and (feedback is None or previous is None)
        ):
            raise ValueError("A different review already exists for this revision")
        review = github.request(
            prefix + "/reviews", {"commit_id": head, "event": "COMMENT", "body": body}
        )
    if (
        not isinstance(review, dict)
        or review.get("commit_id") != head
        or review.get("state") != "COMMENTED"
        or review.get("user", {}).get("login") != reviewer
        or review.get("user", {}).get("type") != "Bot"
        or not (review.get("body") or "").startswith(marker)
    ):
        raise ValueError(
            "Published review differs from its expected identity or revision"
        )
    return {
        **review_receipt(number, review, reviewer),
        "status": "already_posted" if existing else "posted",
    }


def review_receipt(number: int, review: dict, reviewer: str) -> dict:
    marker = MARKER.fullmatch(normalized(review["body"]).split("\n", 1)[0])
    if marker is None or int(marker[1]) != number or marker[2] != review["commit_id"]:
        raise ValueError("Published review differs from its expected revision")
    return {
        "pr_number": number,
        "head_sha": marker[2],
        "baseline_sha": marker[3],
        "review_id": review["id"],
        "review_url": review["html_url"],
        "reviewer_login": reviewer,
        "body_sha256": hashlib.sha256(review["body"].encode()).hexdigest(),
    }
