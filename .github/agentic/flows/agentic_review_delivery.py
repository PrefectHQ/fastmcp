from __future__ import annotations

from uuid import UUID

import httpx
from prefect import task

from flows.agentic_claims import claim_work
from flows.agentic_intake import held
from flows.agentic_review_app import review_client
from flows.agentic_review_publication import before_delivery, post_review, review_body
from flows.agentic_stack import digest
from flows.agentic_stack_policy import Batch, Group, review_revision
from flows.agentic_stack_runtime import (
    Runtime,
    collect,
    owner_alive,
    record,
    saved,
    secret_sync,
)


class PartialReviewDelivery(RuntimeError):
    """Delivery failed after some reviews were already public."""

    def __init__(self, reason: str, posted: list[dict]):
        super().__init__(reason)
        self.posted = posted


@task(retries=2, retry_delay_seconds=[2, 5], persist_result=False)
def deliver_reviews(
    owner: UUID,
    batch: Batch,
    group: Group,
    runtime: Runtime,
    outcome: dict,
    evidence: dict,
) -> dict:
    app = runtime.reviewer_app
    if not runtime.publish_reviews or app is None:
        raise ValueError("Review publication is not configured")
    number = outcome["issue_number"]
    independent = saved(owner, f"{batch.id}-issue-{number}-triage-review")
    if (
        independent is None
        or independent["run_id"] != outcome["triage_review_run_id"]
        or independent["result"] != outcome["adversarial_review"]
    ):
        raise ValueError("Review differs from retained independent evidence")
    verified = outcome.get("verification", [])
    if len({v["pr_number"] for v in verified}) != len(verified):
        raise ValueError("Review publication needs distinct verified contributions")
    if not verified:
        return {
            "posted": [],
            "skipped": [{"reason": "No verified contribution selected"}],
        }
    originals = {pr["number"]: pr for pr in evidence["prs"]}
    receipts = []
    skipped = []
    with review_client(app, secret_sync(app.private_key_block)) as github:
        try:
            for check in verified:
                owner_alive(owner, batch.expires_at)
                pr = originals[check["pr_number"]]
                if pr["state"] != "open" or pr.get("merged") or held(pr):
                    skipped.append(
                        {
                            "pr_number": pr["number"],
                            "reason": "Closed, merged, draft or held contribution",
                        }
                    )
                    continue
                if check["status"] != "verification_recorded":
                    skipped.append(
                        {
                            "pr_number": pr["number"],
                            "reason": "Current contribution verification is incomplete",
                        }
                    )
                    continue
                if (
                    check["status"] != "verification_recorded"
                    or check["head_sha"] != pr["head_sha"]
                    or check["baseline_sha"] != evidence["base_sha"]
                ):
                    raise ValueError(
                        "Contribution verification is unavailable or stale"
                    )
                revision = digest([check["head_sha"], check["baseline_sha"], app.slug])[
                    :24
                ]
                key = f"{batch.id}-pr-{pr['number']}-review-{revision}"
                receipt = saved(owner, key)
                if receipt is not None and any(
                    receipt.get(name) != value
                    for name, value in {
                        "pr_number": pr["number"],
                        "head_sha": check["head_sha"],
                        "baseline_sha": check["baseline_sha"],
                        "reviewer_login": app.slug + "[bot]",
                    }.items()
                ):
                    raise ValueError(
                        "Retained publication receipt has a different revision"
                    )
                if receipt is None:

                    def guard() -> None:
                        owner_alive(owner, batch.expires_at)
                        fresh = collect(number, group.branch)
                        if review_revision(
                            before_delivery(fresh, receipts, app.slug + "[bot]")
                        ) != review_revision(
                            before_delivery(evidence, receipts, app.slug + "[bot]")
                        ):
                            raise ValueError(
                                "Issue or contribution changed after review"
                            )

                    body = review_body(
                        pr["number"], check["head_sha"], check["baseline_sha"], outcome
                    )
                    with claim_work(
                        [f"fastmcp-pr-{pr['number']}-review"], owner, batch.expires_at
                    ):
                        receipt = post_review(
                            github,
                            pr["number"],
                            check["head_sha"],
                            check["baseline_sha"],
                            app.slug + "[bot]",
                            body,
                            guard,
                        )
                    record(owner, key, receipt)
                receipts.append(receipt)
        except (
            ValueError,
            RuntimeError,
            TimeoutError,
            httpx.HTTPError,
            OSError,
        ) as error:
            if not receipts:
                raise
            # Keep reviews that are already public visible to the queue.
            raise PartialReviewDelivery(str(error), receipts) from error
    return {"posted": receipts, "skipped": skipped}
