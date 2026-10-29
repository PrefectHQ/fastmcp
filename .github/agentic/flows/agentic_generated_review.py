from __future__ import annotations

import shlex
from collections.abc import Callable
from urllib.error import HTTPError
from uuid import UUID

import httpx
from prefect import Task, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import State, TaskRun

from flows.agentic_claims import claim_work
from flows.agentic_review_app import review_client
from flows.agentic_review_publication import format_review, post_review
from flows.agentic_stack import StackSpec, digest, read_stack_artifact
from flows.agentic_stack_plans import Review
from flows.agentic_stack_policy import Batch, publication_revision
from flows.agentic_stack_publication import REPOSITORY, GitHub, verify_pr
from flows.agentic_stack_runtime import (
    Runtime,
    api,
    collect,
    owner_alive,
    record,
    saved,
    secret_sync,
)


def deliver_generated_reviews(
    github: GitHub,
    reviewer: str,
    spec: StackSpec,
    artifact: dict,
    publication: dict,
    approval: Review,
    validation_run_id: str,
    review_run_id: str,
    guard: Callable[[], None],
) -> dict:
    if approval.decision != "approved" or approval.findings:
        raise ValueError("Generated drafts require an approved independent review")
    if (
        publication["spec_sha256"] != artifact["spec_sha256"]
        or publication["bundle_sha256"] != artifact["bundle_sha256"]
    ):
        raise ValueError("Draft publication differs from validated review evidence")
    drafts = []
    for number, layer, manifest in zip(
        publication["pull_requests"], spec.layers, artifact["layers"], strict=True
    ):
        head, base = manifest["head_sha"], manifest["parent_sha"]
        checks = "\n".join(
            f"- `{shlex.join(check['command'])}`: exit {check['exit_code']}"
            for check in manifest["checks"]
        )
        details = "\n\n".join(
            [
                f"Reviewed generated draft `{head}` against `{base}` for issue #{layer.issue_number} as part of the proposed chain.",
                approval.reason,
                "Executed by the credential-isolated validator:\n" + checks,
                f"Validation run: `{validation_run_id}`. Independent patch review run: `{review_run_id}`.",
                "Next action: maintainer consideration of the patch and evidence above. This records the independent workflow review; it is not merge approval. The PR remains a draft.",
            ]
        )
        body = format_review(
            number,
            head,
            base,
            f"**Patch review passed** for #{layer.issue_number}. "
            "Executed checks passed. The draft awaits maintainer review.",
            details,
        )
        drafts.append((number, layer, manifest, body))

    def current() -> None:
        guard()
        for number, layer, manifest, body in drafts:
            pr = github.request(f"repos/{REPOSITORY}/pulls/{number}")
            if not isinstance(pr, dict):
                raise ValueError("Published draft disappeared before review delivery")
            verify_pr(pr, spec, layer, manifest)
            if github.pages(
                f"repos/{REPOSITORY}/issues/{number}/comments"
            ) or github.pages(f"repos/{REPOSITORY}/pulls/{number}/comments"):
                raise ValueError(
                    "Generated draft has new discussion after patch review"
                )
            for review in github.pages(f"repos/{REPOSITORY}/pulls/{number}/reviews"):
                if not (
                    review.get("user", {}).get("login") == reviewer
                    and review.get("user", {}).get("type") == "Bot"
                    and review.get("commit_id") == manifest["head_sha"]
                    and review.get("state") == "COMMENTED"
                    and review.get("body") == body
                ):
                    raise ValueError(
                        "Generated draft has new or changed review evidence"
                    )

    posted = []
    for number, _, manifest, body in drafts:
        current()
        posted.append(
            post_review(
                github,
                number,
                manifest["head_sha"],
                manifest["parent_sha"],
                reviewer,
                body,
                current,
                allow_draft=True,
            )
        )
    return {"posted": posted, "skipped": []}


def reviewed_stack(
    runtime: Runtime,
    validation_run_id: UUID,
    review_run_id: str,
    review_parameters: dict,
) -> tuple[StackSpec, dict, Review]:
    spec = StackSpec.model_validate(review_parameters["context"]["spec"])
    artifact = read_stack_artifact.fn(
        validation_run_id,
        runtime.validator_id,
        runtime.source_sha,
        digest(spec.model_dump(mode="json")),
    )
    if (
        artifact["layers"] != review_parameters["context"]["layers"]
        or str(validation_run_id) != review_parameters["context"]["validation_run_id"]
    ):
        raise ValueError("Review does not describe the validated chain")
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(UUID(review_run_id))
        if (
            run.flow_id != runtime.plans["review"].flow_id
            or run.parameters != review_parameters
        ):
            raise ValueError("Stored review used different inputs")
    observation = api.fn("GET", f"/flow_runs/{review_run_id}/execution-plan")
    if observation["status"] != "completed" or observation["snapshot"][
        "execution_plan_version_id"
    ] != str(runtime.plans["review"].version_id):
        raise ValueError("Expected the completed pinned independent review")
    approval = Review.model_validate(
        api.fn("GET", f"/flow_runs/{review_run_id}/execution-plan/outputs/result")[
            "decision"
        ]
    )
    if approval.decision != "approved" or approval.findings:
        raise ValueError("Independent review did not approve this stack")
    return spec, artifact, approval


def retry_delivery(task: Task, task_run: TaskRun, state: State) -> bool:
    error = state.result(raise_on_failure=False)
    if isinstance(error, HTTPError):
        return error.code == 429 or error.code >= 500
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, (OSError, httpx.TransportError))


@task(
    retries=2,
    retry_delay_seconds=[2, 5],
    retry_condition_fn=retry_delivery,
    persist_result=False,
)
def deliver_published_reviews(
    owner: UUID,
    batch: Batch,
    runtime: Runtime,
    publication: dict,
    review_parameters: dict,
) -> dict:
    spec = StackSpec.model_validate(review_parameters["context"]["spec"])
    key = f"{batch.id}-{spec.group}"
    if saved.fn(owner, key + "-published") != publication:
        raise ValueError("Generated review requires the retained publication receipt")
    app = runtime.reviewer_app
    if not runtime.publish_reviews or app is None:
        raise ValueError("Generated review publication is not configured")
    owner_alive.fn(owner, batch.expires_at)
    previous = saved.fn(owner, key + "-generated-reviews")
    if previous is not None:
        return previous
    spec, artifact, approval = reviewed_stack(
        runtime,
        UUID(publication["validation_run_id"]),
        publication["review_run_id"],
        review_parameters,
    )

    def guard() -> None:
        owner_alive.fn(owner, batch.expires_at)
        for item in review_parameters["context"]["items"]:
            current = collect.fn(
                item["evidence"]["issue"]["number"], spec.trunk_ref, publication=True
            )
            if publication_revision(
                current, spec, artifact["layers"]
            ) != publication_revision(item["evidence"]):
                raise ValueError("Issue or contribution evidence changed after review")

    with (
        claim_work(
            [f"fastmcp-pr-{number}-review" for number in publication["pull_requests"]],
            owner,
            batch.expires_at,
        ),
        review_client(app, secret_sync(app.private_key_block)) as github,
    ):
        result = deliver_generated_reviews(
            github,
            app.slug + "[bot]",
            spec,
            artifact,
            publication,
            approval,
            publication["validation_run_id"],
            publication["review_run_id"],
            guard,
        )
    record.fn(owner, key + "-generated-reviews", result)
    return result
