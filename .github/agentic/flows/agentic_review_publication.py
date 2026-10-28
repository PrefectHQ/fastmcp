from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from copy import deepcopy

from flows.agentic_intake import HOLD
from flows.agentic_stack_publication import REPOSITORY, GitHub


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


def review_body(number: int, head: str, baseline: str, result: dict) -> str:
    review = result["adversarial_review"]
    if review["decision"] != "review_existing":
        raise ValueError("Publication requires an independent contribution review")
    marker = (
        f"<!-- fastmcp-triage-review:v1 pr:{number} head:{head} base:{baseline} -->"
    )
    findings = "\n".join(f"- {finding}" for finding in review["findings"])
    return "\n\n".join(
        text
        for text in [
            marker,
            f"Reviewed `{head}` against `{baseline}` for issue #{result['issue_number']}.",
            review["reason"],
            findings,
            "Next action: " + review["next_action"],
            "Independent triage review run: `" + result["triage_review_run_id"] + "`.",
            "This records the checks and limitations above; it is not merge approval.",
        ]
        if text
    )


def post_review(
    github: GitHub,
    number: int,
    head: str,
    baseline: str,
    reviewer: str,
    body: str,
    guard: Callable[[], None],
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
            raise ValueError("PR head changed after review")
        text = "\n".join(
            [pr["title"], pr.get("body") or "", *[x["name"] for x in pr["labels"]]]
        )
        if (
            pr["state"] != "open"
            or pr.get("merged")
            or pr["draft"]
            or HOLD.search(text)
        ):
            raise ValueError("PR is closed, draft or explicitly held")
        if pr["user"]["login"] == reviewer:
            raise ValueError("Reviewer must differ from PR author")
        return pr

    current()
    existing = [
        value
        for value in github.pages(prefix + "/reviews")
        if value.get("user", {}).get("login") == reviewer
        and value.get("user", {}).get("type") == "Bot"
        and (value.get("body") or "").startswith(marker)
    ]
    if len(existing) > 1:
        raise ValueError("Multiple reviews exist for this revision")
    if existing:
        review = existing[0]
    else:
        guard()
        current()
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
        "pr_number": number,
        "head_sha": head,
        "baseline_sha": baseline,
        "review_id": review["id"],
        "review_url": review["html_url"],
        "reviewer_login": reviewer,
        "body_sha256": hashlib.sha256(review["body"].encode()).hexdigest(),
        "status": "already_posted" if existing else "posted",
    }
